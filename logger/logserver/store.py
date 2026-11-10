"""The proxy's recorded traffic, kept on this server's disk and indexed in memory.

The logging proxy offers a small download API, read with its reader key:

    GET /_logs            listing: sealed segments (name, sha256, first, last, bytes) + live
    GET /_logs/<name>     one sealed segment: zstd-compressed JSON lines, one call each
    GET /_logs/live       the calls not sealed yet, plain JSON lines

Segments are saved byte for byte and checked against the sha256 the proxy lists; the live
part is replaced on every sync. Downloads stream to disk, and a segment is never
decompressed whole: production's first one was 2.9 MB holding 190 MB of text. The index
keeps one light ``Call`` per recorded call (metadata and the parts a run sends to the box),
never the whole conversation.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import zstandard

from logserver import calls
from logserver.calls import Call
from logserver.config import Settings


class ProxyError(RuntimeError):
    pass


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.dir = settings.data_dir
        self._sync_lock = threading.Lock()  # one sync at a time, whoever asks
        self._index_lock = threading.Lock()
        self._files: dict[str, tuple[tuple[int, int], list[Call]]] = {}  # name -> stamp, calls
        self._calls: list[Call] = []
        self.status: dict = {"at": None, "error": None, "result": None}

    # ---- download -----------------------------------------------------------------------

    def _open(self, path: str):
        request = urllib.request.Request(
            self.settings.proxy_url + path,
            headers={self.settings.proxy_header: self.settings.proxy_key},
        )
        try:
            return urllib.request.urlopen(request, timeout=60)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise ProxyError("the proxy refused the key") from None
            raise ProxyError(f"the proxy answered HTTP {error.code} for {path}") from None
        except (urllib.error.URLError, TimeoutError) as error:
            raise ProxyError(f"the proxy is not answering: {error}") from None

    def _download(self, path: str, target: Path) -> tuple[str, int]:
        """Stream one download to ``target`` (through a .part file, renamed when complete).
        Returns (sha256, newline count)."""
        part = target.with_name(target.name + ".part")
        digest, lines = hashlib.sha256(), 0
        try:
            with self._open(path) as response, part.open("wb") as out:
                while chunk := response.read(1 << 20):
                    digest.update(chunk)
                    lines += chunk.count(b"\n")
                    out.write(chunk)
            os.replace(part, target)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ProxyError(f"download of {path} failed: {error}") from None
        finally:
            part.unlink(missing_ok=True)
        return digest.hexdigest(), lines

    def sync(self) -> dict:
        """Fetch the listing, download the segments not held yet, refresh the live part,
        then re-index. Returns {"new", "segments", "live_entries", "calls"}."""
        with self._sync_lock:
            try:
                result = self._sync()
            except ProxyError as error:
                self.status.update(error=str(error))
                raise
            result["calls"] = len(self.load())
            self.status.update(at=time.time(), error=None, result=result)
            return result

    def _sync(self) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self._open("/_logs") as response:
            listing = json.loads(response.read())
        new = 0
        for seg in listing.get("segments", []):
            name = seg["name"]
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", name):
                continue
            target = self.dir / name
            if target.exists() and _sha256(target) == seg["sha256"]:
                continue
            sha, _ = self._download(f"/_logs/{name}", target)
            if sha != seg["sha256"]:
                target.unlink()
                raise ProxyError(f"{name}: downloaded bytes do not match the listed sha256")
            new += 1
        _, live_entries = self._download("/_logs/live", self.dir / "live.jsonl")
        return {"new": new, "segments": len(listing.get("segments", [])), "live": live_entries}

    # ---- index --------------------------------------------------------------------------

    def load(self) -> list[Call]:
        """Every recorded call on disk, oldest first, de-duplicated by id (a later file
        wins). A file is parsed again only when it changed; sealed segments never do."""
        with self._index_lock:
            paths = [p for p in sorted(self.dir.glob("*.jsonl.zst")) if p.is_file()]
            live = self.dir / "live.jsonl"
            if live.is_file():
                paths.append(live)
            files: dict[str, tuple[tuple[int, int], list[Call]]] = {}
            changed = False
            for path in paths:
                stat = path.stat()
                stamp = (stat.st_mtime_ns, stat.st_size)
                held = self._files.get(path.name)
                if held and held[0] == stamp:
                    files[path.name] = held
                else:
                    files[path.name] = (stamp, list(_read(path)))
                    changed = True
            if changed or files.keys() != self._files.keys():
                seen: dict[str, Call] = {}
                for name, (_, found) in files.items():
                    for n, call in enumerate(found):
                        seen[call.id or f"{name}:{n}"] = call
                self._files = files
                self._calls = sorted(seen.values(), key=lambda c: c.time)
            return self._calls

    def start(self) -> None:
        """Keep the copy fresh: sync now and then every ``sync_seconds``, forever."""

        def loop() -> None:
            while True:
                try:
                    self.sync()
                except Exception as error:  # the thread must survive anything the proxy does
                    self.status.update(error=str(error))
                time.sleep(self.settings.sync_seconds)

        threading.Thread(target=loop, daemon=True, name="proxy-sync").start()


_segment_lock = threading.Lock()  # one sealed segment decompressing at a time: each holds
# the compressor's window (about 130 MB for production's segments) while it is read


def _read(path: Path):
    """Every call in one log file. A line that does not parse is skipped, never fatal."""
    if path.suffix == ".zst":
        with _segment_lock, path.open("rb") as raw:
            reader = io.BufferedReader(zstandard.ZstdDecompressor().stream_reader(raw))
            yield from _calls(reader)
    else:
        with path.open("rb") as f:
            yield from _calls(f)


def _calls(lines):
    for line in lines:
        if not line.strip():
            continue
        try:
            yield calls.parse(json.loads(line))
        except (ValueError, TypeError, AttributeError):
            continue


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()
