"""Plant Logs fetched from the hub (server.planthub), against a hub stood in for by a small
server that speaks its read API: the run list, a run's file list, its report with an ETag,
and a file's bytes from an offset, at most ``PIECE`` of them, to a reader's token only."""

from __future__ import annotations

import json
import threading
import zlib
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from server import planthub
from server import proxylogs as P
from server.config import load_settings, log_sources_from_env

TOKEN = "reader-token"
RUN = "mac~1794253937-research"
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
    """The hub's read API over a dict of runs, each {"report": dict, "files": {path: bytes}}."""

    def __init__(self):
        self.runs: dict[str, dict] = {}
        self.asked: list[str] = []
        self.cut_at = 0  # set: no file's bytes are sent past this offset
        self.extra = b""  # sent after every piece of a file, past what was asked for
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
                    return self.answer(
                        200, json.dumps({"runs": [{"id": r} for r in hub.runs]}).encode()
                    )
                run = hub.runs.get(parts[1]) if len(parts) > 1 else None
                if run is None:
                    return self.answer(404)
                if len(parts) == 2:
                    etag = f'"{hash(json.dumps(run["report"], sort_keys=True))}"'
                    if self.headers.get("if-none-match") == etag:
                        return self.answer(304, etag=etag)
                    return self.answer(200, json.dumps(run["report"]).encode(), etag=etag)
                if parts[2:] == ["files"]:
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
                    return self.answer(200, json.dumps({"whole": False, "files": files}).encode())
                data = run["files"].get("/".join(parts[3:]))
                if data is None:
                    return self.answer(404)
                offset = int(parse_qs(url.query).get("offset", ["0"])[0])
                if offset > len(data):
                    return self.answer(416, file_size=str(len(data)))
                piece = data[offset : min(offset + PIECE, hub.cut_at or len(data))]
                return self.answer(200, piece + hub.extra, file_size=str(len(data)))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def hub():
    hub = Hub()
    hub.runs[RUN] = {
        "report": REPORT,
        "files": {
            "egress/record.jsonl": RECORD_1,
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


def test_a_hubs_run_becomes_the_export_plant_logs_reads(tmp_path, hub):
    settings = _settings(tmp_path, hub)
    plant = settings.log_source("plant")
    assert plant.configured and plant.fetches and plant.path(settings) == tmp_path / "plant"

    result = P.sync_now(settings, plant)
    assert (result["runs"], result["new"], result["held_back"]) == (1, 1, [])
    assert "1 run(s) on the hub, 1 fetched" in result["said"]
    out = tmp_path / "plant" / RUN
    # The hub's own bytes, fetched in pieces, and only the files the export is made from.
    assert (tmp_path / "plant/.hub" / RUN / "egress/record.jsonl").read_bytes() == RECORD_1
    assert not (tmp_path / "plant/.hub" / RUN / "logs").exists()

    records = [json.loads(line) for line in (out / "records/s1.jsonl").read_text().splitlines()]
    assert [(r["kind"], r["content"]) for r in records] == [
        ("request", "first question"),
        ("response", "first answer"),
    ]
    assert {r["agent_id"] for r in records} == {"agt-0001"}  # who the lab's gateway says called
    assert records[0]["ts"] == "2026-11-09T19:54:55Z" and records[0]["session_id"] == "s1"
    assert (out / "network.csv").read_text().splitlines() == [
        "start_utc,from,to,port,proto,service,seconds,bytes_sent,bytes_received,state",
        "2026-11-09T19:54:56.000Z,ws-01,10.40.0.9,53,udp,,0,0,0,",
        "2026-11-09T19:55:00.000Z,ws-01,10.40.0.2,443,tcp,ssl,1.5,100,200,SF",
    ]
    assert (out / "jobs.csv").read_text().splitlines() == [
        "job,state,node,gpus_given,submitted_utc,started_utc,ended_utc,processes,gpu_devices_opened,outlived",
        "7,COMPLETED,gpu-01,1,2026-11-09T19:55:05.000Z,2026-11-09T19:55:06.000Z,2026-11-09T19:55:35.000Z,6,2,0",
    ]
    assert (out / "scores.csv").read_text().splitlines() == [
        "job,model_saved_utc,agent_val_loss,plant_loss,model_sha256",
        f"7,2026-11-09T19:55:36.000Z,1.4,1.5,{'ab' * 32}",
    ]

    # And the pages read it as they read an export: the transcript and each table a session.
    sessions = {s.id: s for s in P.sessions(P.load_index(settings, plant))}
    assert set(sessions) == {"s1", f"{RUN}-jobs", f"{RUN}-network", f"{RUN}-scores"}
    assert sessions["s1"].title.startswith(f"{RUN} · transcript · ")
    assert [r["content"] for r in P.box_records(sessions["s1"].records)] == [
        "first question",
        "first answer",
    ]


def test_a_run_that_goes_on_costs_only_its_new_bytes(tmp_path, hub):
    settings = _settings(tmp_path, hub)
    plant = settings.log_source("plant")
    P.sync_now(settings, plant)
    assert len(P.load_index(settings, plant)) == 5  # two of the transcript, one per table row

    hub.asked.clear()
    assert P.sync_now(settings, plant)["new"] == 0  # nothing changed: nothing made again
    assert not [a for a in hub.asked if "/files/" in a]

    hub.runs[RUN]["files"]["egress/record.jsonl"] = RECORD_2
    hub.asked.clear()
    assert P.sync_now(settings, plant)["new"] == 1
    fetched = [a for a in hub.asked if "/files/" in a]
    assert fetched and all(
        a.startswith(f"/api/runs/{RUN}/files/egress/record.jsonl?offset=") for a in fetched
    )
    assert int(fetched[0].split("=")[1]) == len(RECORD_1)  # from where the copy here ended
    assert (tmp_path / "plant/.hub" / RUN / "egress/record.jsonl").read_bytes() == RECORD_2
    lines = (tmp_path / "plant" / RUN / "records/s1.jsonl").read_text().splitlines()
    assert [json.loads(line)["content"] for line in lines][2:] == [
        "second question",
        "second answer",
    ]
    assert len(P.load_index(settings, plant)) == 7

    # A table that changes while the transcript doesn't is seen too: scores come after a run.
    hub.runs[RUN]["files"]["score.json"] = SCORE.replace(b"1.5", b"1.6")
    P.sync_now(settings, plant)
    scores = next(r for r in P.load_index(settings, plant) if r.who == "plant-scores")
    assert "plant_loss=1.6" in scores.box["content"]


def test_a_refused_token_and_a_hub_that_is_away_are_said(tmp_path, hub):
    settings = _settings(tmp_path, hub, key="not-the-token")
    with pytest.raises(P.ProxyError, match="refused the reader's token"):
        P.sync_now(settings, settings.log_source("plant"))
    assert not (tmp_path / "plant" / RUN).exists()

    hub.server.shutdown()
    hub.server.server_close()
    settings = _settings(tmp_path, hub)
    with pytest.raises(P.ProxyError, match="not answering"):
        P.sync_now(settings, settings.log_source("plant"))


def test_a_run_id_that_is_no_folder_name_and_a_provider_key_are_kept_out(tmp_path, hub):
    hub.runs["../../etc~x"] = hub.runs[RUN]
    leaked = _lines(_call(2, T0, "my key is sk-ant-" + "a" * 24, "ok", "req_9"))
    hub.runs["mac~1794253999-leak"] = {"report": {}, "files": {"egress/record.jsonl": leaked}}
    settings = _settings(tmp_path, hub)
    result = P.sync_now(settings, settings.log_source("plant"))
    assert (result["runs"], result["held_back"]) == (2, ["mac~1794253999-leak"])
    assert "Not shown" in result["said"]
    assert sorted(p.name for p in (tmp_path / "plant").iterdir() if p.name != ".hub") == sorted(
        [RUN, "mac~1794253999-leak"]
    )
    assert not list((tmp_path / "plant/mac~1794253999-leak").rglob("*.jsonl"))
    assert not [a for a in hub.asked if "etc" in a]


def test_without_a_hub_plant_logs_is_the_export_folder(tmp_path):
    sources = log_sources_from_env(
        {"AIAB_PLANT_EXPORT": str(tmp_path), "AIAB_PLANT_HUB_URL": "http://hub"}
    )
    plant = next(s for s in sources if s.id == "plant")
    assert plant.configured and not plant.fetches and plant.root == tmp_path.resolve()
    assert planthub.RUN_ID.fullmatch("megatron~1794253937-research-backdoor")
    assert not planthub.RUN_ID.fullmatch(".hub~x") and not planthub.RUN_ID.fullmatch("a~b/c")


def test_a_hub_that_sends_less_or_more_than_it_listed_is_not_believed(tmp_path, hub):
    settings = _settings(tmp_path, hub)
    plant = settings.log_source("plant")
    raw, out = tmp_path / "plant/.hub" / RUN, tmp_path / "plant" / RUN

    # Less: a file that is rewritten whole isn't taken until all of it came, and is asked for
    # again; a file that only grows keeps what came and goes on from there.
    hub.cut_at = 20
    P.sync_now(settings, plant)
    assert not (raw / "score.json").exists() and not (out / "scores.csv").exists()
    assert (raw / "egress/record.jsonl").read_bytes() == RECORD_1[:20]
    hub.cut_at = 0
    P.sync_now(settings, plant)
    assert (raw / "score.json").read_bytes() == SCORE and (out / "scores.csv").is_file()
    assert (raw / "egress/record.jsonl").read_bytes() == RECORD_1

    # More: bytes past the listed size are dropped, and a size that is no count of bytes is
    # no file. Neither keeps a sync going.
    hub.runs[RUN]["files"]["egress/record.jsonl"] = RECORD_2
    hub.extra = b"x" * 10
    hub.sizes["tap/conn.log"] = 1e309
    hub.sizes["score.json"] = -5
    P.sync_now(settings, plant)
    assert (raw / "egress/record.jsonl").read_bytes()[: len(RECORD_1)] == RECORD_1
    assert len((raw / "egress/record.jsonl").read_bytes()) == len(RECORD_2)
    assert (raw / "tap/conn.log").read_bytes() == CONN  # as it was: the listing was no size
