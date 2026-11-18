"""A proxy read through its log server: metadata here, sealed records for a run (MAC-377).

The stand-in below answers the log server's API (``logger/`` in this repository) and seals
with the box's own code, so a test can open what a run would send.
"""

import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from aiab.baked.sealing import RecordDecryptor, seal_record

from server import proxylogs as P
from server import runs
from server.config import load_settings, log_sources_from_env

KEY = "ui-key"
SECRET = "the launch code is 0451"
ANSWER = "I will not repeat it"
CALLS = {  # session id -> [(call id, ts, [(kind, text)])]
    "s-alice": [
        ("a1", "2027-01-15T08:00:00Z", [("request", SECRET), ("response", ANSWER)]),
        ("a2", "2027-01-15T08:01:00Z", [("tool_call", "ls -la")]),
    ],
    "s-bob": [("b1", "2027-01-15T09:00:00Z", [("request", "hello")])],
}


class LogServer:
    def __init__(self):
        self.decryptor = RecordDecryptor()
        self.boot_id = "boot-1"
        self.synced = 0
        box = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def answer(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def allowed(self):
                if self.headers.get("x-log-key") == KEY:
                    return True
                self.answer(401, {"detail": "missing or wrong x-log-key"})
                return False

            def do_GET(self):
                if self.allowed():
                    self.answer(200, {"whos": ["alice", "bob"], "sessions": box.sessions()})

            def do_POST(self):
                if not self.allowed():
                    return
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/v1/sync":
                    box.synced += 1
                    return self.answer(200, {"new": 1, "segments": 2, "live": 3, "calls": 3})
                unknown = [i for i in body["sessions"] if i not in CALLS]
                if unknown:
                    detail = {"error": "unknown_sessions", "sessions": unknown}
                    return self.answer(404, {"detail": detail})
                plain = box.plain(body["sessions"], body.get("offset", 0))
                sealed = [seal_record(r, box.decryptor.public_key, box.boot_id) for r in plain]
                self.answer(200, {"boot_id": box.boot_id, "records": sealed})

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.http.server_address[1]}"
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def sessions(self):
        out = []
        for sid, calls in CALLS.items():
            who = sid.removeprefix("s-")
            out.append(
                {
                    "id": sid,
                    "who": who,
                    "calls": [
                        {
                            "id": cid,
                            "ts": ts,
                            "who": who,
                            "status": 200,
                            "duration_s": 1.5,
                            "model": "claude-haiku-5-5",
                            "stop_reason": "end_turn",
                            "request_bytes": 300,
                            "messages": 1,
                            "records": len(parts),
                            "kinds": [k for k, _ in parts],
                            "content_bytes": sum(len(t.encode()) for _, t in parts),
                        }
                        for cid, ts, parts in calls
                    ],
                }
            )
        return out

    def plain(self, ids, first=0):
        out = []
        for sid in ids:
            for _, ts, parts in CALLS[sid]:
                for kind, text in parts:
                    out.append(
                        {
                            "stream": "proxy",
                            "batch": 0,
                            "offset": first + len(out),
                            "agent_id": sid.removeprefix("s-"),
                            "session_id": sid,
                            "ts": ts,
                            "kind": kind,
                            "content": text,
                        }
                    )
        return out


@pytest.fixture
def server():
    server = LogServer()
    yield server
    server.http.shutdown()


def _settings(tmp_path, server, key=KEY):
    env = {"AIAB_PROXY_SERVER_URL": server.url + "/", "AIAB_PROXY_SERVER_KEY": key}
    settings = replace(load_settings(), logs_dir=tmp_path, log_sources=log_sources_from_env(env))
    return settings, settings.log_source("dev")


def test_a_source_is_read_through_its_log_server_only_when_both_settings_are_set():
    both = {"AIAB_LOG_SERVER_URL": "https://logs.example/", "AIAB_LOG_SERVER_KEY": "k"}
    prod, dev = log_sources_from_env({**both, "AIAB_PROXY_KEY": "p"})[:2]
    assert (prod.kind, prod.url, prod.header, prod.key) == (
        "logger",
        "https://logs.example",
        "x-log-key",
        "k",
    )
    assert prod.fetches and prod.configured and prod.label == "Prod Agent Logs"
    assert dev.kind == "proxy"  # the other source is untouched
    half = log_sources_from_env({"AIAB_LOG_SERVER_URL": "https://logs.example"})[0]
    assert half.kind == "proxy"


def test_sessions_come_as_metadata_and_nothing_readable_is_kept(tmp_path, server):
    settings, source = _settings(tmp_path, server)
    assert P.load_index(settings, source) == []  # nothing until the first sync
    result = P.sync_now(settings, source)
    assert result["new"] == 1 and result["live_entries"] == 3 and server.synced == 1
    found = {s.id: s for s in P.sessions(P.load_index(settings, source))}
    assert set(found) == {"s-alice", "s-bob"}
    alice = found["s-alice"]
    assert alice.who == "alice" and len(alice.records) == 2 and alice.model == "claude-haiku-5-5"
    assert "2 calls" in alice.title and SECRET not in alice.title
    built = P.box_records(alice.records)  # counted by kind; there is no text to hold
    assert [r["kind"] for r in built] == ["request", "response", "tool_call"]
    assert {r["content"] for r in built} == {""}
    assert P.sent_kb(alice.records, built) == round(
        (len(SECRET) + len(ANSWER) + len("ls -la")) / 1024, 1
    )
    assert SECRET not in repr(alice) and not list(tmp_path.rglob("*"))  # nothing on disk


def test_a_run_gets_its_records_sealed_and_only_the_box_opens_them(tmp_path, server):
    settings, source = _settings(tmp_path, server)
    P.sync_now(settings, source)
    alice = next(s for s in P.sessions(P.load_index(settings, source)) if s.id == "s-alice")
    sealed = P.run_records([(source, alice)])
    assert len(sealed) == 3 and all(r["enc"] and r["boot_id"] == "boot-1" for r in sealed)
    assert SECRET not in json.dumps(sealed)
    assert server.decryptor.unseal(sealed, "boot-1") == server.plain(["s-alice"])


def test_sealed_and_local_records_share_a_run_without_two_at_one_position(tmp_path, server):
    settings, source = _settings(tmp_path, server)
    P.sync_now(settings, source)
    alice = next(s for s in P.sessions(P.load_index(settings, source)) if s.id == "s-alice")
    entry = {
        "id": "x1",
        "time": 1_800_000_000.0,
        "who": "carol",
        "request_headers": {"x-claude-code-session-id": "s-carol"},
        "request_raw": {
            "text": json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}]})
        },
        "response_raw": {"text": "{}"},
    }
    other = replace(source, id="other", kind="proxy")
    carol = P.sessions([P.parse_request(entry)])[0]
    records = P.run_records([(source, alice), (other, carol)])
    positions = [(r["stream"], r["batch"], r["offset"]) for r in records]
    assert len(set(positions)) == len(positions) == 4
    assert [("enc" in r) for r in records] == [True, True, True, False]
    assert records[3]["content"] == "hi" and records[3]["offset"] == 3


def test_a_wrong_key_and_a_session_gone_are_said_plainly(tmp_path, server):
    settings, source = _settings(tmp_path, server, key="wrong")
    with pytest.raises(P.ProxyError, match="refused the key"):
        P.sync(settings, source)
    settings, source = _settings(tmp_path, server)
    gone = P.Session(id="s-gone", who="x", records=[])
    with pytest.raises(P.ProxyError, match="no longer holds these sessions: s-gone"):
        P.run_records([(source, gone)])


def test_the_export_step_seals_only_what_is_not_sealed_and_stops_on_a_restarted_box(tmp_path):
    box = RecordDecryptor()
    identity = {"encryption_key": box.public_key, "boot_id": "boot-1"}
    plain = [
        {
            "stream": "proxy",
            "batch": 0,
            "offset": i,
            "agent_id": "a",
            "session_id": "s",
            "ts": "2027-01-15T08:00:00Z",
            "kind": "request",
            "content": text,
        }
        for i, text in enumerate([SECRET, ANSWER])
    ]
    arrived = seal_record(plain[0], box.public_key, "boot-1")
    fixture = tmp_path / "records.jsonl"
    fixture.write_text("".join(json.dumps(r) + "\n" for r in (arrived, plain[1])))
    assert runs._seal_here(fixture, identity) == 1
    written = [json.loads(line) for line in fixture.read_text().splitlines()]
    assert written[0] == arrived and "enc" in written[1]  # the sealed one is left as it came
    assert ANSWER not in fixture.read_text()
    assert box.unseal(written, "boot-1") == plain
    with pytest.raises(RuntimeError, match="restarted"):
        runs._seal_here(fixture, {**identity, "boot_id": "boot-2"})
    # A box before MAC-377 publishes no key: plain records stay plain.
    fixture.write_text(json.dumps(plain[1]) + "\n")
    assert runs._seal_here(fixture, {"boot_id": "boot-1"}) == 0
    assert json.loads(fixture.read_text()) == plain[1]


def test_logs_and_runs_pages_list_a_log_servers_sessions(tmp_path, server, monkeypatch):
    from fastapi.testclient import TestClient

    from server import app as web
    from server.auth import Account

    settings, source = _settings(tmp_path, server)
    P.sync_now(settings, source)
    monkeypatch.setattr(web, "settings", settings)
    client = TestClient(web.app)
    client.cookies.set(web.COOKIE, web.signer.issue(Account("lab", "lab", "Lab", "unused")))
    page = client.get("/logs?logs=dev").text
    assert "alice" in page and "bob" in page and SECRET not in page
    listed = client.get("/logs/sessions.json?logs=dev").json()["sessions"]
    assert {(s["id"], s["records"]) for s in listed} == {("s-alice", 3), ("s-bob", 1)}
    one = client.get("/logs?logs=dev&session=s-alice").text
    assert "claude-haiku-5-5" in one and SECRET not in one
    assert client.get("/logs/raw/a1?logs=dev").status_code == 404
