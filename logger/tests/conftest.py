"""A proxy data directory written the way the logging proxy writes it (its segments.py),
and a stand-in box (the box's own decryptor behind a fake attested client), so the tests run
the real reading, index and sealing code with nothing mocked in between."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import zstandard
from aiab.baked.sealing import RecordDecryptor
from fastapi.testclient import TestClient

from logserver.app import create_app
from logserver.box import Box
from logserver.config import Settings
from logserver.store import Store

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


def compress(data: bytes) -> bytes:
    """As the proxy's segments.compress(): long-range zstd, 128 MiB window, with a checksum."""
    params = zstandard.ZstdCompressionParameters.from_level(
        3, window_log=27, enable_ldm=True, write_checksum=True, write_content_size=True
    )
    return zstandard.ZstdCompressor(compression_params=params).compress(data)


class ProxyDir:
    """The proxy's data directory: requests.jsonl, and segments/ once it first rotates.
    Each method does what the proxy does, in the same steps."""

    def __init__(self, root: Path):
        self.root = root
        self.live = root / "requests.jsonl"
        self.segments = root / "segments"
        root.mkdir(parents=True)
        self.live.touch()
        self.seq = 0  # the last seq written
        self.live_first = 1

    def append(self, entries=(), raw: bytes = b"") -> None:
        """Entries appended to the live file, then any raw bytes (a torn line, say)."""
        with self.live.open("ab") as f:
            f.write(lines(entries) + raw)
        self.seq += len(entries) + raw.count(b"\n")

    def rotate(self) -> Path:
        """segments.rotate(): the live file renamed to pending, then a fresh live file."""
        self.segments.mkdir(exist_ok=True)
        pending = self.segments / f"requests-{self.live_first:09d}-{self.seq:09d}.jsonl"
        os.replace(self.live, pending)
        fresh = self.live.with_name("requests.jsonl.new")
        fresh.touch()
        os.replace(fresh, self.live)
        self.live_first = self.seq + 1
        return pending

    def compress(self, pending: Path, keep_pending: bool = False) -> Path:
        """segments.compress(): .zst.tmp written, renamed into place, then pending deleted."""
        final = pending.with_name(pending.name + ".zst")
        tmp = pending.with_name(pending.name + ".zst.tmp")
        tmp.write_bytes(compress(pending.read_bytes()))
        os.replace(tmp, final)
        if not keep_pending:
            pending.unlink()
        return final

    def seal(self, entries) -> Path:
        self.append(entries)
        return self.compress(self.rotate())


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
def proxy(tmp_path):
    """Two calls sealed (seqs 1-2), one live (seq 3)."""
    proxy = ProxyDir(tmp_path / "proxy-data")
    proxy.seal(SEALED)
    proxy.append(LIVE)
    return proxy


@pytest.fixture
def settings(proxy):
    return Settings(
        proxy_dir=proxy.root,
        api_key=API_KEY,
        box_host="box.example",
        box_repo="org/config",
        sync_seconds=300,
    )


@pytest.fixture
def fakebox():
    return FakeBox()


@pytest.fixture
def store(settings):
    store = Store(settings)
    store.refresh()
    return store


@pytest.fixture
def client(settings, store, fakebox):
    box = Box(settings.box_host, settings.box_repo, connect=fakebox.connect)
    app = create_app(settings, store=store, box=box, background=False)
    return TestClient(app, headers={"x-log-key": API_KEY})
