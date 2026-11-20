"""Plant Logs read from the plant's hub (server.planthub), against a hub stood in for by a
small server that speaks its read API: the run list with an ETag, a run's file list, its
report, and a file's bytes from an offset, at most ``PIECE`` of them, to a reader's token
only. The pages keep the hub's list of runs and nothing of a run; a run fetches the parts
it sends, a record at a time (MAC-528)."""

from __future__ import annotations

import json
import threading
import zlib
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from fastapi.testclient import TestClient

from server import app as ui
from server import planthub, runs
from server import proxylogs as P
from server.config import load_settings, log_sources_from_env

TOKEN = "reader-token"
RUN = "mac~1794253937-research"
SESSION = "mac_1794253937-research"  # the run's id in the letters the box takes
PIECE = 64  # small, so every file here takes several requests


def _call(seq: int, t: float, said: str, answer: str, request_id: str) -> list[dict]:
    """One model call as the plant's wall records it: the request, then the reply in pieces."""
    body = json.dumps({"model": "claude-test", "messages": [{"role": "user", "content": said}]})
    events = [
        ("message_start", {"type": "message_start", "message": {"model": "claude-test"}}),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
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
    headers = {"x-claude-code-session-id": "s1"}
    return [
        {
            "kind": "request",
            "seq": seq,
            "t_ns": ns,
            "src": "10.40.0.12",
            "path": "/v1/messages?beta=true",
            "headers": headers,
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
    + _call(2, T0, "first question", "first answer", "req_1")
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
    """The hub's read API over a dict of runs, each {"report": dict, "files": {path: bytes}}
    and, when given, "state", "whole" and "broken" (its file list answers 500)."""

    def __init__(self):
        self.runs: dict[str, dict] = {}
        self.asked: list[str] = []
        self.cut_at = 0  # set: no file's bytes are sent past this offset
        self.extra = b""  # sent after a file's last piece: it grew since it was listed
        self.sizes: dict[str, object] = {}  # a file's listed size, when not its real one
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
                if self.headers.get("authorization") != f"Bearer {TOKEN}":
                    return self.answer(401)
                url = urlsplit(self.path)
                parts = [unquote(p) for p in url.path.split("/")[2:]]  # after /api
                if parts == ["runs"]:
                    listed = [
                        {
                            "id": name,
                            "site": name.partition("~")[0],
                            "state": run.get("state", "finished"),
                            "started": T0,
                            "stopped": T0 + 120,
                            "sessions": [{"session": 1}],
                        }
                        for name, run in hub.runs.items()
                    ]
                    body = json.dumps({"machine": "hub", "runs": listed}).encode()
                    etag = f'"{zlib.crc32(body)}"'
                    if self.headers.get("if-none-match") == etag:
                        return self.answer(304, etag=etag)
                    return self.answer(200, body, etag=etag)
                run = hub.runs.get(parts[1]) if len(parts) > 1 else None
                if run is None:
                    return self.answer(404)
                if len(parts) == 2:
                    return self.answer(200, json.dumps(run["report"]).encode())
                if parts[2:] == ["files"]:
                    if run.get("broken"):
                        return self.answer(500, b"a record the plant cannot read")
                    files = [
                        {
                            "path": p,
                            "growth": "APPEND" if p.endswith((".jsonl", ".log")) else "REPLACE",
                            "size": hub.sizes.get(p, len(b)),
                            "changed_ns": zlib.crc32(b),
                            "sha256": None,
                        }
                        for p, b in sorted(run["files"].items())
                    ]
                    listing = {"whole": run.get("whole", True), "files": files}
                    return self.answer(200, json.dumps(listing).encode())
                data = run["files"].get("/".join(parts[3:]))
                if data is None:
                    return self.answer(404)
                offset = int(parse_qs(url.query).get("offset", ["0"])[0])
                if offset > len(data):
                    return self.answer(416, file_size=str(len(data)))
                piece = data[offset : min(offset + PIECE, hub.cut_at or len(data))]
                grown = hub.extra if offset + len(piece) >= len(data) else b""
                return self.answer(200, piece + grown, file_size=str(len(data)))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def bytes_asked(self) -> list[str]:
        """The requests for a file's bytes: what the pages must never make."""
        return [a for a in self.asked if "/files/" in a]


@pytest.fixture
def hub():
    hub = Hub()
    hub.runs[RUN] = {
        "report": REPORT,
        "files": {
            "egress/record.jsonl": RECORD_2,
            "tap/conn.log": CONN,
            "config/compose.json": COMPOSE,
            "score.json": SCORE,
            "logs/web.log": b"not fetched",
        },
    }
    yield hub
    hub.server.shutdown()


def _settings(tmp_path, hub, key=TOKEN):
    env = {
        "AIAB_PLANT_HUB_URL": hub.url + "/",
        "AIAB_PLANT_HUB_KEY": key,
        "AIAB_PLANT_EXPORT": "/ignored",
    }
    return replace(load_settings(), logs_dir=tmp_path, log_sources=log_sources_from_env(env))


def _sessions(settings, plant):
    return {s.id: s for s in P.sessions(P.load_index(settings, plant))}


def _written(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_the_pages_keep_the_hubs_list_of_runs_and_nothing_of_a_run(tmp_path, hub):
    settings = _settings(tmp_path, hub)
    plant = settings.log_source("plant")
    assert plant.configured and plant.fetches and plant.hub
    assert plant.path(settings) == tmp_path / "plant"

    result = P.sync_now(settings, plant)
    assert (result["runs"], result["new"], result["unread"]) == (1, 1, [])
    assert "1 run(s) on the hub, 1 new" in result["said"]
    # On the disk: the list and its ETag. No file of the run, no copy, no export.
    assert sorted(p.name for p in (tmp_path / "plant").iterdir()) == ["hub.json", "runs.jsonl"]
    assert not hub.bytes_asked() and f"/api/runs/{RUN}" not in hub.asked  # nor its report
    listed = (tmp_path / "plant/runs.jsonl").read_text()
    assert "question" not in listed and "answer" not in listed
    entry = json.loads(listed)
    assert (entry["id"], entry["site"], entry["state"], entry["whole"]) == (
        RUN,
        "mac",
        "finished",
        True,
    )
    assert entry["files"]["egress/record.jsonl"] == len(RECORD_2)

    # Each part of the run is a session with no text: what the lists and a Log Source name.
    sessions = _sessions(settings, plant)
    assert set(sessions) == {
        f"{SESSION}-{part}" for part in ("transcript", "jobs", "network", "scores")
    }
    transcript = sessions[f"{SESSION}-transcript"]
    assert transcript.who == "mac" and transcript.title.startswith("research · transcript · ")
    assert transcript.title.endswith(f"{len(RECORD_2) / 1024:.1f} KB")
    assert P.on_hub(transcript.records) and P.sendable(transcript.records)
    assert P.box_records(transcript.records) == []  # nothing is built here
    assert P.sent_kb(transcript.records, []) == round(len(RECORD_2) / 1024, 1)
    # Nothing is kept in memory between two pages: the list is read from the disk again.
    assert P.load_index(settings, plant) is not P.load_index(settings, plant)


def test_a_run_that_is_whole_is_not_asked_about_again_and_one_going_is(tmp_path, hub):
    settings = _settings(tmp_path, hub)
    plant = settings.log_source("plant")
    P.sync_now(settings, plant)

    hub.asked.clear()
    assert P.sync_now(settings, plant)["new"] == 0
    assert hub.asked == ["/api/runs"]  # the list said it is as it was; nothing else is asked

    # A second run that is still going: its sizes are asked for at every sync, the first
    # run's never again.
    live = "mac~1794260000-train"
    hub.runs[live] = {
        "report": {},
        "state": "live",
        "whole": False,
        "files": {"egress/record.jsonl": RECORD_1},
    }
    hub.asked.clear()
    assert P.sync_now(settings, plant)["new"] == 1
    assert hub.asked == ["/api/runs", f"/api/runs/{live}/files"]
    hub.runs[live]["files"]["egress/record.jsonl"] = RECORD_2
    hub.asked.clear()
    P.sync_now(settings, plant)
    assert hub.asked == ["/api/runs", f"/api/runs/{live}/files"]
    grown = _sessions(settings, plant)["mac_1794260000-train-transcript"]
    assert grown.records[0].size == len(RECORD_2) and grown.records[0].state == "live"
    assert not hub.bytes_asked()


def test_a_run_fetches_the_chosen_parts_a_record_at_a_time(tmp_path, hub):
    settings = _settings(tmp_path, hub)
    plant = settings.log_source("plant")
    P.sync_now(settings, plant)
    sessions = _sessions(settings, plant)
    target = tmp_path / "records.jsonl"

    # Only the Slurm jobs: the hub's report holds them, so no file of the run is fetched.
    hub.asked.clear()
    assert P.write_run_records([(plant, sessions[f"{SESSION}-jobs"])], target) == 1
    assert not hub.bytes_asked()
    (job,) = _written(target)
    assert (job["agent_id"], job["session_id"], job["kind"]) == (
        "plant-jobs",
        f"{SESSION}-jobs",
        "tool_result",
    )
    assert job["content"].startswith("job=7, state=COMPLETED, node=gpu-01, gpus_given=1, ")
    assert job["content"].endswith("processes=6, gpu_devices_opened=2, outlived=0")

    # Everything: each part in a batch of its own, the transcript's calls in order.
    hub.asked.clear()
    picks = [(plant, sessions[f"{SESSION}-{p}"]) for p in ("transcript", "network", "scores")]
    assert P.write_run_records(picks, target) == 6
    asked = {a.split("?")[0].split("/files/")[1] for a in hub.bytes_asked()}
    assert asked == {"egress/record.jsonl", "tap/conn.log", "config/compose.json", "score.json"}
    assert len(hub.bytes_asked()) > 20  # in the hub's pieces, never a file at once
    records = _written(target)
    said = [r for r in records if r["session_id"] == f"{SESSION}-transcript"]
    assert [(r["kind"], r["content"]) for r in said] == [
        ("request", "first question"),
        ("response", "first answer"),
        ("request", "second question"),
        ("response", "second answer"),
    ]
    assert {r["agent_id"] for r in said} == {"agt-0001"}  # who the lab's gateway says called
    assert said[0]["ts"] == "2026-11-09T19:54:55Z" and [r["offset"] for r in said] == [0, 1, 2, 3]
    network = next(r for r in records if r["session_id"] == f"{SESSION}-network")
    assert network["content"].splitlines() == [
        "start_utc,from,to,port,proto,service,seconds,bytes_sent,bytes_received,state",
        "2026-11-09T19:55:00.000Z,ws-01,10.40.0.2,443,tcp,ssl,1.5,100,200,SF",
        "2026-11-09T19:54:56.000Z,ws-01,10.40.0.9,53,udp,,0,0,0,",
    ]
    scores = next(r for r in records if r["session_id"] == f"{SESSION}-scores")
    assert scores["content"] == (
        f"job=7, model_saved_utc=2026-11-09T19:55:36.000Z, agent_val_loss=1.4, "
        f"plant_loss=1.5, model_sha256={'ab' * 32}"
    )
    # The box refuses two records at one position: no two parts share a batch.
    assert len({(r["batch"], r["offset"]) for r in records}) == len(records)
    assert len({r["batch"] for r in records}) == 3
    assert sorted(p.name for p in (tmp_path / "plant").iterdir()) == ["hub.json", "runs.jsonl"]


def test_a_long_run_is_never_held_whole(tmp_path, hub, monkeypatch):
    """Two hundred calls arrive in pieces of 64 bytes: each is written before the next is
    read, so what is held is one piece and one call, whatever the run's length."""
    calls = [c for i in range(200) for c in _call(10 * i + 2, T0 + i, f"q{i}", f"a{i}", f"r{i}")]
    hub.runs[RUN]["files"] = {"egress/record.jsonl": _lines(calls)}
    settings = _settings(tmp_path, hub)
    plant = settings.log_source("plant")
    P.sync_now(settings, plant)
    going, most = [], []
    real = planthub._calls

    def watched(records):
        def lines():
            for r in records:
                going.append(r)
                yield r

        for call in real(lines()):
            most.append(len(going))
            going.clear()
            yield call

    monkeypatch.setattr(planthub, "_calls", watched)
    target = tmp_path / "records.jsonl"
    session = _sessions(settings, plant)[f"{SESSION}-transcript"]
    assert P.write_run_records([(plant, session)], target) == 400
    assert max(most) == 5  # the five lines of one call, never more
    assert [r["content"] for r in _written(target)][-2:] == ["q199", "a199"]


def test_a_refused_token_and_a_hub_that_is_away_are_said(tmp_path, hub):
    settings = _settings(tmp_path, hub, key="not-the-token")
    with pytest.raises(P.ProxyError, match="refused the reader's token"):
        P.sync_now(settings, settings.log_source("plant"))
    assert not (tmp_path / "plant/runs.jsonl").exists()

    hub.server.shutdown()
    hub.server.server_close()
    settings = _settings(tmp_path, hub)
    with pytest.raises(P.ProxyError, match="not answering"):
        P.sync_now(settings, settings.log_source("plant"))


def test_a_run_the_hub_cannot_read_stops_no_other(tmp_path, hub):
    hub.runs["mac~1794250000-broken"] = {"report": {}, "files": {}, "broken": True}
    hub.runs["../../etc~x"] = hub.runs[RUN]  # no run id: never asked about
    settings = _settings(tmp_path, hub)
    plant = settings.log_source("plant")
    result = P.sync_now(settings, plant)
    assert (result["runs"], result["unread"]) == (2, ["mac~1794250000-broken"])
    assert "could not read 1: mac~1794250000-broken" in result["said"]
    assert planthub.unread(tmp_path / "plant") == ["mac~1794250000-broken"]
    assert {r.run for r in P.load_index(settings, plant)} == {RUN}  # the good one is listed
    assert not [a for a in hub.asked if "etc" in a]
    hub.runs["mac~1794250000-broken"]["broken"] = False  # asked again at the next sync
    hub.runs["mac~1794250000-broken"]["files"] = {"score.json": SCORE}
    assert P.sync_now(settings, plant)["unread"] == []
    assert len({r.run for r in P.load_index(settings, plant)}) == 2


def test_a_provider_key_in_a_run_is_not_sent_and_nothing_is_left(tmp_path, hub):
    leaked = _lines(_call(2, T0, "my key is sk-ant-" + "a" * 24, "ok", "req_9"))
    hub.runs[RUN]["files"]["egress/record.jsonl"] = RECORD_1 + leaked
    settings = _settings(tmp_path, hub)
    plant = settings.log_source("plant")
    P.sync_now(settings, plant)
    target = tmp_path / "records.jsonl"
    session = _sessions(settings, plant)[f"{SESSION}-transcript"]
    with pytest.raises(P.ProxyError, match="a provider key is in the records"):
        P.write_run_records([(plant, session)], target)
    assert not target.exists() and not list(tmp_path.glob("records.jsonl*"))


def test_a_hub_that_sends_less_or_more_than_it_listed_is_not_believed(tmp_path, hub):
    settings = _settings(tmp_path, hub)
    plant = settings.log_source("plant")
    P.sync_now(settings, plant)
    target = tmp_path / "records.jsonl"
    session = _sessions(settings, plant)[f"{SESSION}-transcript"]

    # Less: a run is never sent in part.
    hub.cut_at = 20
    with pytest.raises(P.ProxyError, match="the hub sent 20 of the"):
        P.write_run_records([(plant, session)], target)
    assert not target.exists()

    # More: bytes past the listed size are dropped. A size that is no count of bytes is no
    # file, so that part has nothing to send.
    hub.cut_at = 0
    hub.extra = b"x" * 10
    assert P.write_run_records([(plant, session)], target) == 4
    hub.sizes["tap/conn.log"] = 1e309
    network = _sessions(settings, plant)[f"{SESSION}-network"]
    assert P.write_run_records([(plant, network)], target) == 0


def test_without_a_hub_plant_logs_is_the_export_folder(tmp_path):
    sources = log_sources_from_env(
        {"AIAB_PLANT_EXPORT": str(tmp_path), "AIAB_PLANT_HUB_URL": "http://hub"}
    )
    plant = next(s for s in sources if s.id == "plant")
    assert plant.configured and not plant.fetches and not plant.hub
    assert plant.root == tmp_path.resolve()
    assert planthub.RUN_ID.fullmatch("megatron~1794253937-research-backdoor")
    assert not planthub.RUN_ID.fullmatch(".hub~x") and not planthub.RUN_ID.fullmatch("a~b/c")
    # A run's name too long for the box is cut, and two such stay apart.
    long = "p" * 100 + "~" + "f" * 100
    assert len(planthub.session_id(long, "transcript")) <= 128
    assert planthub.session_id(long, "jobs") != planthub.session_id(long + "x", "jobs")


def test_the_export_step_reads_a_run_of_any_length_a_record_at_a_time(tmp_path):
    """The run's own file is counted and sealed without being loaded, and the scope is read
    from the head of the submission, not by parsing every record in it."""
    record = {
        "stream": "proxy",
        "batch": 0,
        "agent_id": "a",
        "session_id": "s",
        "ts": "2027-01-15T08:00:00Z",
        "kind": "request",
        "content": "said",
    }
    fixture = tmp_path / "records.jsonl"
    fixture.write_text("".join(json.dumps({**record, "offset": i}) + "\n" for i in range(5000)))
    assert runs._seal_here(fixture, {"boot_id": "boot-1"}) == (0, 5000)
    sent = runs.sent_sessions(tmp_path, "plant", [{"id": "s", "title": "t"}], fixture)
    assert sent["sessions"] == [{"id": "s", "title": "t", "records": 5000, "kept": None}]

    scope = {"agents": ["a"], "window_start": "2027-01-15T08:00:00Z"}
    submission = tmp_path / "submission.json"
    submission.write_text(
        json.dumps({"input_scope": scope, "records": ["not", "read"]}, separators=(",", ":"))
    )
    assert runs._input_scope(submission) == scope
    submission.write_text(json.dumps({"records": [], "input_scope": scope}))  # another order
    assert runs._input_scope(submission) == scope


def test_the_pages_list_hub_runs_and_a_run_sends_what_it_fetched(tmp_path, hub, monkeypatch):
    settings = replace(_settings(tmp_path / "logs", hub), runs_dir=tmp_path / "runs")
    plant = settings.log_source("plant")
    P.sync_now(settings, plant)
    monkeypatch.setattr(ui, "settings", settings)
    monkeypatch.setattr(ui, "user_of", lambda request: {"username": "lab", "party": "lab"})
    client = TestClient(ui.app, follow_redirects=False)

    page = client.get("/logs?logs=plant").text
    assert "1 run on the hub" in page and "research · transcript" in page
    assert f"{len(RECORD_2) / 1024:.1f} KB" in page and "question" not in page
    one = client.get(f"/logs?logs=plant&session={SESSION}-transcript").text
    assert "Nothing of this run is kept here" in one and RUN in one
    listing = client.get("/logs/sessions.json?logs=plant").json()
    assert {s["id"] for s in listing["sessions"]} == {
        f"{SESSION}-{part}" for part in ("transcript", "jobs", "network", "scores")
    }
    assert not hub.bytes_asked()  # three pages, and still nothing of the run was fetched

    started = {}

    async def ready(_folder):
        return {"ok": True, "missing": [], "release": None, "plan_digest": "x"}

    def fake_start(settings, user, agreement, folder, records, label=None, sent=None):
        started.update(records=_written(records), label=label, sent=sent)
        records.unlink()  # as the run takes the file for its own
        return "run-1"

    plan = tmp_path / "plan"
    plan.mkdir()

    class Target:
        plan_dir = plan

    monkeypatch.setattr(ui, "_run_preflight", ready)
    monkeypatch.setattr(ui.pv, "for_agreement", lambda settings, name: Target)
    monkeypatch.setattr(ui.runs, "start_run", fake_start)
    monkeypatch.setattr(ui.pv, "record_change", lambda *a, **k: None)
    monkeypatch.setattr(ui.runs, "plan_sessions", lambda folder: {f"{SESSION}-transcript"})
    response = client.post(
        "/runs/start",
        data={"agreement": "bio", "logs": "plant", "session": f"{SESSION}-transcript"},
    )
    assert response.status_code == 303 and response.headers["location"] == "/runs/run-1"
    assert [r["content"] for r in started["records"]][:2] == ["first question", "first answer"]
    assert "4 records" in started["label"]
    assert started["sent"]["sessions"][0]["records"] == 4
    assert not list((tmp_path / "runs").iterdir())  # no file of the run is left behind

    # Larger on the hub than a run may send: refused by its size there, nothing fetched.
    monkeypatch.setattr(ui, "settings", replace(settings, max_send_mb=0.00001))
    hub.asked.clear()
    started.clear()
    response = client.post(
        "/runs/start",
        data={"agreement": "bio", "logs": "plant", "session": f"{SESSION}-transcript"},
    )
    assert "run%20can%20send" in response.headers["location"].replace("+", "%20")
    assert not started and not hub.bytes_asked()
