"""The RSI plant's runs as this server's source (logserver.plant), against a hub stood in for
by a small server that speaks its read API: the run list, a run's file list, its report with
an ETag, and a file's bytes from an offset, at most ``PIECE`` of them, to a reader's token
only. The stand-in and its data follow the agreement UI's tests/test_planthub.py."""

from __future__ import annotations

import json
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from aiab.baked import records as box_records
from conftest import API_KEY, LIVE, PROD_KEY, SEALED, SECRET, FakeBox, ProxyDir
from fastapi.testclient import TestClient

from logserver import config
from logserver.app import create_app
from logserver.box import Box
from logserver.config import Settings, Target
from logserver.plant import PlantStore
from logserver.sources import Sources
from logserver.store import Store, StoreError

TOKEN = "reader-token"
RUN = "mac~1794253937-research"
PIECE = 64  # small, so every file here takes several requests
SAID = "the plant's secret question"  # what was said; must never leave unsealed


def _call(seq: int, t: float, said: str, answer: str, request_id: str, session="s1") -> list[dict]:
    """One model call as the plant's wall records it: the request, then the reply in pieces."""
    body = json.dumps({"model": "claude-test", "messages": [{"role": "user", "content": said}]})
    events = [
        ("message_start", {"type": "message_start", "message": {"model": "claude-test"}}),
        (
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": answer},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}),
    ]
    reply = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)
    ns = int(t * 1e9)
    return [
        {
            "kind": "request",
            "seq": seq,
            "t_ns": ns,
            "src": "10.40.0.12",
            "path": "/v1/messages?beta=true",
            "headers": {"x-claude-code-session-id": session} if session else {},
            "body": body,
        },
        {
            "kind": "response_head",
            "seq": seq + 1,
            "req_seq": seq,
            "t_ns": ns,
            "status": 200,
            "request_id": request_id,
        },
        {"kind": "response_chunk", "seq": seq + 2, "req_seq": seq, "t_ns": ns, "data": reply[:40]},
        {"kind": "response_chunk", "seq": seq + 3, "req_seq": seq, "t_ns": ns, "data": reply[40:]},
        {"kind": "response_end", "seq": seq + 4, "req_seq": seq, "t_ns": ns + 2_000_000_000},
    ]


def _lines(rows: list[dict]) -> bytes:
    return "".join(json.dumps(r) + "\n" for r in rows).encode()


T0 = 1794254095.0
COUNT = {"kind": "request", "seq": 90, "t_ns": 1, "path": "/v1/messages/count_tokens", "body": "{}"}
RECORD_1 = _lines(
    [{"kind": "wall_start", "seq": 1, "t_ns": 1}, COUNT]
    + _call(2, T0, SAID, "first answer", "req_1")
)
RECORD_2 = RECORD_1 + _lines(_call(10, T0 + 60, "second question", "second answer", "req_2"))
COMPOSE = json.dumps(
    {"services": {"ws-01": {"networks": {"lab": {"ipv4_address": "10.40.0.12"}}}}}
).encode()
CONN = _lines(
    [
        {
            "ts": T0 + 5,
            "id.orig_h": "10.40.0.12",
            "id.resp_h": "10.40.0.2",
            "id.resp_p": 443,
            "proto": "tcp",
            "service": "ssl",
            "duration": 1.5,
            "orig_bytes": 100,
            "resp_bytes": 200,
            "conn_state": "SF",
        },
        {
            "ts": T0 + 1,
            "id.orig_h": "10.40.0.12",
            "id.resp_h": "10.40.0.9",
            "id.resp_p": 53,
            "proto": "udp",
        },
    ]
)
REPORT = {
    "jobs": [
        {
            "id": 7,
            "state": "COMPLETED",
            "nodes": "gpu-01",
            "gpus": 1,
            "submitted": T0 + 10,
            "started": T0 + 11,
            "ended": T0 + 40,
        }
    ],
    "sensor": {
        "jobs": {
            "7": {
                "processes": 6,
                "gpu_devices_opened": ["/dev/nvidia0", "/dev/nvidiactl"],
                "outlived": 0,
            }
        }
    },
    "tap": {
        "calls": [
            {"request_id": "req_1", "gateway_identity": "agt-0001"},
            {"request_id": "req_2", "gateway_identity": "agt-0001"},
        ]
    },
}
SCORE = json.dumps(
    {
        "jobs": [
            {
                "job": 7,
                "saved_at": T0 + 41,
                "sha256": "ab" * 32,
                "loss": 1.5,
                "claim": {"val_loss": 1.4},
            }
        ]
    }
).encode()


class Hub:
    """The hub's read API over a dict of runs, each {"report": dict, "files": {path: bytes}}."""

    def __init__(self):
        self.runs: dict[str, dict] = {}
        self.asked: list[str] = []
        self.redirect = ""  # set: every request is answered with a redirect there
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def answer(self, status: int, body: bytes = b"", **headers: str):
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name.replace("_", "-"), value)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                hub.asked.append(self.path)
                if hub.redirect:
                    return self.answer(302, location=hub.redirect)
                if self.headers.get("authorization") != f"Bearer {TOKEN}":
                    return self.answer(401)
                url = urlsplit(self.path)
                parts = [unquote(p) for p in url.path.split("/")[2:]]  # after /api
                if parts == ["runs"]:
                    return self.answer(
                        200, json.dumps({"runs": [{"id": r} for r in hub.runs]}).encode()
                    )
                run = hub.runs.get(parts[1]) if len(parts) > 1 else None
                if run is None:
                    return self.answer(404)
                if len(parts) == 2:
                    etag = f'"{zlib.crc32(json.dumps(run["report"], sort_keys=True).encode())}"'
                    if self.headers.get("if-none-match") == etag:
                        return self.answer(304, etag=etag)
                    return self.answer(200, json.dumps(run["report"]).encode(), etag=etag)
                if parts[2:] == ["files"]:
                    files = [
                        {
                            "path": p,
                            "growth": "APPEND" if p.endswith((".jsonl", ".log")) else "REPLACE",
                            "size": len(b),
                            "changed_ns": zlib.crc32(b),
                            "sha256": None,
                        }
                        for p, b in sorted(run["files"].items())
                    ]
                    return self.answer(200, json.dumps({"whole": False, "files": files}).encode())
                data = run["files"].get("/".join(parts[3:]))
                if data is None:
                    return self.answer(404)
                offset = int(parse_qs(url.query).get("offset", ["0"])[0])
                if offset > len(data):
                    return self.answer(416, file_size=str(len(data)))
                return self.answer(200, data[offset : offset + PIECE], file_size=str(len(data)))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def _files(record: bytes = RECORD_1) -> dict[str, bytes]:
    return {
        "egress/record.jsonl": record,
        "tap/conn.log": CONN,
        "config/compose.json": COMPOSE,
        "score.json": SCORE,
        "logs/web.log": b"not fetched",
    }


@pytest.fixture
def hub():
    hub = Hub()
    hub.runs[RUN] = {"report": REPORT, "files": _files()}
    yield hub
    hub.server.shutdown()


@pytest.fixture
def settings(tmp_path, hub):
    return Settings(
        proxy_dir=tmp_path / "no-proxy",
        boxes=(Target("dev", API_KEY, "box.example", "org/config"),),
        sync_seconds=300,
        hub_url=hub.url,
        hub_key=TOKEN,
        data_dir=tmp_path / "data",
    )


@pytest.fixture
def store(settings):
    store = PlantStore(settings)
    store.refresh()
    return store


@pytest.fixture
def fakebox():
    return FakeBox()


@pytest.fixture
def client(settings, store, fakebox):
    boxes = {"dev": Box("box.example", "org/config", connect=fakebox.connect)}
    app = create_app(settings, store=store, boxes=boxes, background=False)
    return TestClient(app, headers={"x-log-key": API_KEY})


def _sessions(client) -> dict[str, dict]:
    return {s["id"]: s for s in client.get("/v1/sessions").json()["sessions"]}


def test_a_run_is_its_transcript_and_three_tables_as_metadata(client, settings, store):
    assert store.status["result"] == {"new": 1, "runs": 1, "held_back": [], "calls": 4}
    # The hub's own bytes, fetched in pieces, and only the files the sessions are made from.
    raw = settings.data_dir / "hub" / RUN
    assert (raw / "egress/record.jsonl").read_bytes() == RECORD_1
    assert not (raw / "logs").exists()

    response = client.get("/v1/sessions")
    assert SAID not in response.text and "first answer" not in response.text
    assert "10.40.0.2" not in response.text and "COMPLETED" not in response.text
    body = response.json()
    assert body["whos"] == ["agt-0001", "plant-jobs", "plant-network", "plant-scores"]
    sessions = {s["id"]: s for s in body["sessions"]}
    run = "mac_1794253937-research"  # the run's id as the box takes one
    assert set(sessions) == {"s1", f"{run}-jobs", f"{run}-network", f"{run}-scores"}
    transcript = sessions["s1"]
    assert (transcript["who"], transcript["run"], transcript["records"]) == ("agt-0001", RUN, 2)
    assert transcript["model"] == "claude-test"
    call = transcript["calls"][0]
    assert (call["id"], call["status"], call["duration_s"]) == (f"{RUN}:2", 200, 2.0)
    assert call["kinds"] == ["request", "response"] and call["stop_reason"] == "end_turn"
    for table in ("jobs", "network", "scores"):
        session = sessions[f"{run}-{table}"]
        assert (session["who"], session["run"], session["records"]) == (f"plant-{table}", RUN, 1)
        assert session["calls"][0]["kinds"] == ["tool_result"]
    assert sessions[f"{run}-jobs"]["started"] == "2026-11-09T19:55:06Z"  # when job 7 started


def test_an_export_is_sealed_and_the_box_opens_what_the_plant_recorded(client, fakebox):
    run = "mac_1794253937-research"
    wanted = ["s1", f"{run}-jobs", f"{run}-network", f"{run}-scores"]
    response = client.post("/v1/export", json={"sessions": wanted})
    assert response.status_code == 200
    assert SAID not in response.text and "gpu-01" not in response.text
    sealed = response.json()["records"]
    # The box takes them: the ids are its own kind of id, and no position is used twice.
    box_records.export_statement(sealed, box_records.scope_of(sealed))
    opened = fakebox.decryptor.unseal(sealed, fakebox.boot_id)
    by_session: dict[str, list[dict]] = {}
    for record in opened:
        by_session.setdefault(record["session_id"], []).append(record)
    assert [(r["kind"], r["content"], r["agent_id"]) for r in by_session["s1"]] == [
        ("request", SAID, "agt-0001"),
        ("response", "first answer", "agt-0001"),
    ]
    assert by_session[f"{run}-jobs"][0]["content"] == (
        "job=7, state=COMPLETED, node=gpu-01, gpus_given=1, "
        "submitted_utc=2026-11-09T19:55:05.000Z, started_utc=2026-11-09T19:55:06.000Z, "
        "ended_utc=2026-11-09T19:55:35.000Z, processes=6, gpu_devices_opened=2, outlived=0"
    )
    # Connections oldest first, the lab's machines by name where the lab names them.
    assert by_session[f"{run}-network"][0]["content"].splitlines() == [
        "start_utc,from,to,port,proto,service,seconds,bytes_sent,bytes_received,state",
        "2026-11-09T19:54:56.000Z,ws-01,10.40.0.9,53,udp,,0,0,0,",
        "2026-11-09T19:55:00.000Z,ws-01,10.40.0.2,443,tcp,ssl,1.5,100,200,SF",
    ]
    assert by_session[f"{run}-scores"][0]["content"] == (
        "job=7, model_saved_utc=2026-11-09T19:55:36.000Z, agent_val_loss=1.4, "
        f"plant_loss=1.5, model_sha256={'ab' * 32}"
    )
    assert sorted(r["offset"] for r in opened) == list(range(5))
    assert all(r["kind"] in ("request", "response", "tool_result") for r in opened)


def test_the_index_holds_no_text_and_an_export_reads_only_the_calls_it_sends(store, hub, settings):
    hub.runs[RUN]["files"]["egress/record.jsonl"] = RECORD_2
    store.refresh()
    assert all(c.parts == () and c.sizes for c in store.calls())
    second = [c for c in store.calls() if c.id == f"{RUN}:10"]
    read = store.with_text(second)
    assert [c.parts for c in read] == [
        (("request", "second question"), ("response", "second answer"))
    ]
    assert read[0].at[1] >= len(RECORD_1)  # found in the record from where its request starts

    (settings.data_dir / "hub" / RUN / "egress/record.jsonl").write_bytes(b"")
    with pytest.raises(StoreError, match="not where they were"):
        store.with_text(second)


def test_a_run_that_grew_costs_only_its_new_bytes(store, hub, settings):
    hub.asked.clear()
    assert store.refresh()["new"] == 0  # nothing changed: nothing fetched, nothing read again
    assert not any("/files/" in path for path in hub.asked)

    hub.runs[RUN]["files"]["egress/record.jsonl"] = RECORD_2
    hub.asked.clear()
    result = store.refresh()
    assert (result["new"], result["calls"]) == (1, 5)
    record = [p for p in hub.asked if "record.jsonl" in p]
    assert record and all(int(p.split("offset=")[1]) >= len(RECORD_1) for p in record)
    assert (settings.data_dir / "hub" / RUN / "egress/record.jsonl").read_bytes() == RECORD_2


def test_a_restart_reads_what_is_on_its_disk_without_fetching_it_again(store, hub, settings):
    hub.asked.clear()
    again = PlantStore(settings)
    assert again.refresh()["calls"] == 4
    assert not any("/files/" in path for path in hub.asked)


def test_a_run_holding_a_provider_key_is_held_back_and_keeps_being_named(hub, settings):
    leaked = _lines(_call(2, T0, "my key is sk-ant-api03-" + "a" * 24, "noted", "req_1"))
    hub.runs["mac~leaky"] = {"report": {}, "files": {"egress/record.jsonl": leaked}}
    store = PlantStore(settings)
    for _ in range(2):  # named on every read, not only the one that found it
        result = store.refresh()
        assert result["held_back"] == ["mac~leaky"]
        assert store.status["problems"] == [
            {"file": "mac~leaky", "problem": "held back: its records hold a provider key"}
        ]
    assert {c.run for c in store.calls()} == {RUN}


def test_a_call_with_no_session_id_is_its_runs_and_its_agents(hub, settings):
    for plant in ("a", "b"):  # two plants, one agent name, the same minute
        record = _lines(_call(2, T0, f"asked on {plant}", "answered", "req_1", session=""))
        hub.runs[f"{plant}~run"] = {"report": REPORT, "files": {"egress/record.jsonl": record}}
    store = PlantStore(settings)
    store.refresh()
    sessions = {c.session_id for c in store.calls() if c.who == "agt-0001" and c.run != RUN}
    assert sessions == {"a_run-agt-0001", "b_run-agt-0001"}


def test_a_hub_that_is_down_or_refuses_the_token_says_so(tmp_path, hub, settings, client):
    from dataclasses import replace

    wrong = PlantStore(replace(settings, hub_key="not-the-token", data_dir=tmp_path / "other"))
    with pytest.raises(StoreError, match="refused the reader's token"):
        wrong.refresh()
    assert "refused" in wrong.status["error"] and wrong.calls() == ()

    hub.server.shutdown()
    hub.server.server_close()
    response = client.post("/v1/sync")
    assert response.status_code == 503 and "not answering" in response.json()["detail"]
    assert len(_sessions(client)) == 4  # what was read before is still served


def test_a_redirect_is_never_followed_with_the_token(hub, settings):
    other = Hub()
    try:
        hub.redirect = other.url + "/api/runs"
        store = PlantStore(settings)
        with pytest.raises(StoreError, match="HTTP 302"):
            store.refresh()
        assert other.asked == []
    finally:
        other.server.shutdown()


@pytest.fixture
def both(tmp_path, hub):
    """One server reading a proxy's directory and the hub. The dev box sees both; the
    production box is kept to the proxy."""
    proxy = ProxyDir(tmp_path / "proxy-data")
    proxy.seal(SEALED)
    proxy.append(LIVE)
    settings = Settings(
        proxy_dir=proxy.root,
        boxes=(
            Target("dev", API_KEY, "box.example", "org/config"),
            Target("prod", PROD_KEY, "prod.example", "org/prod-config", sources=("proxy",)),
        ),
        sync_seconds=300,
        sources=("proxy", "plant"),
        hub_url=hub.url,
        hub_key=TOKEN,
        data_dir=tmp_path / "data",
    )
    store = Sources({"proxy": Store(settings), "plant": PlantStore(settings)})
    store.refresh()
    fakes = {"dev": FakeBox(), "prod": FakeBox()}
    boxes = {
        t.name: Box(t.box_host, t.box_repo, connect=fakes[t.name].connect) for t in settings.boxes
    }
    app = create_app(settings, store=store, boxes=boxes, background=False)
    return TestClient(app, headers={"x-log-key": API_KEY}), fakes, store


def test_one_server_reads_the_proxy_and_the_plant_and_exports_from_both(both):
    client, fakes, store = both
    run = "mac_1794253937-research"
    assert set(_sessions(client)) == {
        "s-alice",
        "bob-1800000500",
        "s1",
        f"{run}-jobs",
        f"{run}-network",
        f"{run}-scores",
    }
    status = client.get("/v1/status").json()
    assert (status["calls"], status["sources"]) == (7, ["proxy", "plant"])
    # The proxy's own fields stay where the agreement UI reads them; each source has its own.
    assert status["result"]["segments"] == 1 and status["result"]["live"] == 1
    assert status["result"]["plant"]["runs"] == 1 and status["result"]["calls"] == 7
    assert client.post("/v1/sync").json()["new"] == 0

    response = client.post("/v1/export", json={"sessions": ["s-alice", "s1"]})
    assert response.status_code == 200 and SECRET not in response.text
    opened = fakes["dev"].decryptor.unseal(response.json()["records"], fakes["dev"].boot_id)
    assert [(r["session_id"], r["content"]) for r in opened] == [
        ("s1", SAID),
        ("s1", "first answer"),
        ("s-alice", SECRET),
        ("s-alice", "I will not repeat the launch code"),
        ("s-alice", "and the second question"),
        ("s-alice", "second answer"),
    ]


def test_a_box_kept_to_the_proxy_neither_sees_nor_exports_the_plant(both):
    client, _, _ = both
    prod = {"x-log-key": PROD_KEY}
    listed = client.get("/v1/sessions", headers=prod).json()
    assert {s["id"] for s in listed["sessions"]} == {"s-alice", "bob-1800000500"}
    assert listed["whos"] == ["alice", "bob"]
    status = client.get("/v1/status", headers=prod).json()
    assert (status["calls"], status["sources"]) == (3, ["proxy"])
    refused = client.post("/v1/export", json={"sessions": ["s1"]}, headers=prod)
    assert refused.status_code == 404


def test_one_source_down_does_not_hide_the_other(both, hub):
    client, _, store = both
    hub.server.shutdown()
    hub.server.server_close()
    response = client.post("/v1/sync")
    assert response.status_code == 503 and "plant: the hub is not answering" in response.text
    assert store.status["error"].startswith("plant: ")
    assert len(_sessions(client)) == 6  # the proxy was read, and the plant's last read stays


def test_the_sources_follow_the_settings_and_a_box_can_be_kept_to_some():
    base = {"API_KEY": "k", "BOX_HOST": "box.example", "BOX_REPO": "org/config"}
    hub = {"PLANT_HUB_URL": "https://hub.example", "PLANT_HUB_KEY": "token"}
    assert config.load(base).sources == ("proxy",)
    assert config.load({**base, **hub}).sources == ("plant",)  # a hub and no proxy directory
    both = config.load({**base, **hub, "PROXY_DIR": "/proxy-data", "SOURCES": "proxy"})
    assert both.sources == ("proxy", "plant") and both.boxes[0].sources == ("proxy",)
    listed = {
        **hub,
        "PROXY_DIR": "/proxy-data",
        "BOXES": "dev,prod",
        "DEV_API_KEY": "d",
        "DEV_BOX_HOST": "dev.example",
        "DEV_BOX_REPO": "org/dev",
        "PROD_API_KEY": "p",
        "PROD_BOX_HOST": "prod.example",
        "PROD_BOX_REPO": "org/prod",
        "PROD_SOURCES": "proxy",
    }
    assert [b.sources for b in config.load(listed).boxes] == [("proxy", "plant"), ("proxy",)]
    with pytest.raises(config.ConfigError, match="not a source this server reads"):
        config.load({**base, "SOURCES": "plant"})


def test_the_two_hub_settings_come_together_or_not_at_all():
    base = {"API_KEY": "k", "BOX_HOST": "box.example", "BOX_REPO": "org/config"}
    assert config.load(base).hub_url == ""
    loaded = config.load(
        {**base, "PLANT_HUB_URL": "https://hub.example/", "PLANT_HUB_KEY": " token "}
    )
    assert (loaded.hub_url, loaded.hub_key) == ("https://hub.example", "token")
    assert "token" not in repr(loaded)
    for env in ({"PLANT_HUB_URL": "https://hub.example"}, {"PLANT_HUB_KEY": "token"}):
        with pytest.raises(config.ConfigError, match="together"):
            config.load({**base, **env})
    with pytest.raises(config.ConfigError, match="not an http"):
        config.load({**base, "PLANT_HUB_URL": "hub.example", "PLANT_HUB_KEY": "token"})
