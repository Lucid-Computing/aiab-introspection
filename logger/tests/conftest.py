"""A stand-in logging proxy (the three /_logs routes) and a stand-in box (the box's own
decryptor behind a fake attested client), so the tests run the real download, index and
sealing code with nothing mocked in between."""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import zstandard
from aiab.baked.sealing import RecordDecryptor
from fastapi.testclient import TestClient

from logserver.app import create_app
from logserver.box import Box
from logserver.config import Settings
from logserver.store import Store

PROXY_KEY = "proxy-reader-key"
API_KEY = "ui-key"
SECRET = "the launch code is 0451"  # what was said; must never leave unsealed
ANSWER = "I will not repeat the launch code"


def entry(id, who, t, text, answer, session=""):
    headers = {"x-claude-code-session-id": session} if session else {}
    request = {"model": "claude-haiku-5-5", "messages": [{"role": "user", "content": text}]}
    reply = {"content": [{"type": "text", "text": answer}], "stop_reason": "end_turn"}
    return {
        "id": id,
        "who": who,
        "time": t,
        "duration_s": 1.5,
        "status": 200,
        "request_headers": headers,
        "request_raw": {"text": json.dumps(request)},
        "response_raw": {"text": json.dumps(reply)},
    }


SEALED = [
    entry("a1", "alice", 1_800_000_000.0, SECRET, ANSWER, "s-alice"),
    entry("a2", "alice", 1_800_000_060.0, "and the second question", "second answer", "s-alice"),
]
LIVE = [entry("b1", "bob", 1_800_000_500.0, "bob asks something", "bob is answered")]


def lines(entries) -> bytes:
    return b"".join(json.dumps(e).encode() + b"\n" for e in entries)


class Proxy:
    def __init__(self):
        self.segment = zstandard.ZstdCompressor().compress(lines(SEALED))
        self.name = "llmproxy-test-x-0-1.jsonl.zst"
        self.sha = hashlib.sha256(self.segment).hexdigest()
        self.live = lines(LIVE)
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.headers.get("x-log-key") != PROXY_KEY:
                    return self._send(401, b"{}")
                if self.path == "/_logs":
                    seg = {"name": proxy.name, "sha256": proxy.sha, "first": 0, "last": 1}
                    return self._send(200, json.dumps({"segments": [seg]}).encode())
                if self.path == f"/_logs/{proxy.name}":
                    return self._send(200, proxy.segment)
                if self.path == "/_logs/live":
                    return self._send(200, proxy.live)
                self._send(404, b"{}")

            def _send(self, code, body):
                self.send_response(code)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


class FakeBox:
    """The box's side: its decryptor, a boot id, and how often it was attested."""

    def __init__(self):
        self.restart()
        self.attested = 0
        self.down = False

    def restart(self):
        self.decryptor = RecordDecryptor()
        self.boot_id = f"boot-{id(self.decryptor)}"
        self.generation = getattr(self, "generation", 0) + 1

    def connect(self, host, repo):
        if self.down:
            raise ConnectionError("no box")
        self.attested += 1
        box, generation = self, self.generation

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"encryption_key": box.decryptor.public_key, "boot_id": box.boot_id}

        class Client:
            def get(self, url, timeout=None):
                if box.generation != generation:  # the pinned TLS key is gone with the old boot
                    raise ConnectionError("certificate does not match the pinned key")
                assert url == f"https://{host}/aiab/pubkey"
                return Response()

        return Client()


@pytest.fixture
def proxy():
    proxy = Proxy()
    yield proxy
    proxy.server.shutdown()


@pytest.fixture
def settings(proxy, tmp_path):
    return Settings(
        proxy_url=proxy.url,
        proxy_header="x-log-key",
        proxy_key=PROXY_KEY,
        api_key=API_KEY,
        box_host="box.example",
        box_repo="org/config",
        data_dir=tmp_path / "data",
        sync_seconds=300,
    )


@pytest.fixture
def fakebox():
    return FakeBox()


@pytest.fixture
def store(settings):
    store = Store(settings)
    store.sync()
    return store


@pytest.fixture
def client(settings, store, fakebox):
    box = Box(settings.box_host, settings.box_repo, connect=fakebox.connect)
    app = create_app(settings, store=store, box=box, background=False)
    return TestClient(app, headers={"x-log-key": API_KEY})
