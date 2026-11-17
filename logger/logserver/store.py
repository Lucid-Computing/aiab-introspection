"""The proxy's recorded traffic, read in place from its data directory and indexed in memory.

The logging proxy's volume is mounted here read-only (``PROXY_DIR``). Its log is one stream
of JSON lines, one per call, kept in three kinds of files (the proxy's ``segments.py``):

    segments/requests-<first>-<last>.jsonl.zst   sealed: zstd-compressed, seqs first..last
    segments/requests-<first>-<last>.jsonl       pending: moved aside, not compressed yet
    requests.jsonl                               live: the proxy appends here

The live file's first seq is the last sealed seq + 1. The proxy rotates by renaming the
live file to pending and creating a new live file, then compresses the pending file into
its ``.zst`` and deletes it. So the live file is opened before ``segments/`` is listed:
if a rotation happens in between, the open handle no longer is ``requests.jsonl``, and the
read starts again (the worm-outbox reads the same files the same way, logger-outbox.md §3.2).

Nothing is ever written to the proxy's directory, and nothing is copied to this server's
disk. A sealed or pending file is parsed once and kept by its seq range, so a pending file
that becomes its ``.zst`` is not parsed again; the live file is read on from where the last
read stopped, up to its last complete line. A file that cannot be read is skipped and
reported in the status, never the whole load. The index keeps one light ``Call`` per
recorded call (metadata and the parts a run sends to the box), never the whole conversation.

A background thread re-reads every ``SYNC_SECONDS``; requests answer from the last index.
"""

from __future__ import annotations

import bisect
import hashlib
import io
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import zstandard

from logserver import calls
from logserver.calls import Call, Where
from logserver.config import Settings

LIVE = "requests.jsonl"
SEGMENTS = "segments"
NAME_RE = re.compile(r"^requests-(\d{9})-(\d{9})\.jsonl(\.zst)?$")  # the proxy's segments.NAME_RE
WINDOW_LOG = 27  # the proxy compresses with a 128 MiB window
OPEN_TRIES = 3  # rotations caught between opening the live file and listing segments/


class StoreError(RuntimeError):
    """The proxy's directory cannot be read at all."""


class GoneError(RuntimeError):
    """Chosen calls are in files the proxy no longer has: they can be listed, not read."""

    def __init__(self, calls: list[Call]):
        self.calls = calls
        super().__init__("no file holds " + ", ".join(_named(c) for c in calls))


class ChangedError(RuntimeError):
    """A chosen call no longer reads as it did when it was indexed."""


class _Vanished(Exception):
    """A listed file went before it was opened: look again."""

    def __init__(self, calls: list[Call]):
        self.calls = calls


@dataclass(frozen=True)
class Container:
    """A sealed or pending file, from its name."""

    first: int
    last: int
    path: Path
    compressed: bool

    @property
    def name(self) -> str:
        return f"{SEGMENTS}/{self.path.name}"


@dataclass(frozen=True)
class Parsed:
    """What one sealed or pending file held. A file that could not be read holds no calls."""

    name: str
    calls: tuple[tuple[str, Call], ...]  # (key, call): the key is the call id, or its place
    unreadable: int  # lines that are not JSON, or not a call
    error: str | None = None


@dataclass
class Live:
    """How far the live file has been read. ``identity`` is (first seq, device, inode)."""

    identity: tuple[int, int, int] | None = None
    offset: int = 0  # bytes taken: whole lines only
    lines: int = 0
    unreadable: int = 0
    calls: list[tuple[str, Call]] = field(default_factory=list)  # (key, call), as in Parsed


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.dir = settings.proxy_dir
        self._refresh_lock = threading.Lock()  # one read at a time, whoever asks
        self._parsed: dict[tuple[int, int], Parsed] = {}  # (first, last) -> what it held
        self._live = Live()
        self._live_first = 0  # the highest live first seq seen
        self._backwards: int | None = None  # a lower one, seen once: wait for a second look
        self._calls: tuple[Call, ...] = ()
        # Files a read-back found changed: the next refresh parses them again.
        self._stale: set[tuple[int, int] | str] = set()
        self._stale_lock = threading.Lock()
        # Replaced whole, never changed in place, so a request reads it without the lock.
        self.status: dict = {"at": None, "error": None, "result": None, "problems": []}

    def calls(self) -> tuple[Call, ...]:
        """Every recorded call, in the order of the stream, de-duplicated by id. Never reads
        a file: this is the index the last refresh built."""
        return self._calls

    def refresh(self) -> dict:
        """Read what is new in the proxy's directory and rebuild the index.
        Returns {"new", "segments", "live", "calls"}."""
        with self._refresh_lock:
            try:
                try:
                    result, problems = self._refresh()
                except OSError as error:  # not a single file's problem: the read failed
                    raise StoreError(f"reading {self.dir} failed: {error}") from None
            except StoreError as error:
                self.status = {**self.status, "error": str(error)}
                raise
            self.status = {"at": time.time(), "error": None, "result": result, "problems": problems}
            return result

    def start(self) -> None:
        """Read now, then every ``sync_seconds``, forever."""

        def loop() -> None:
            while True:
                try:
                    self.refresh()
                except Exception as error:  # the thread must survive anything on the disk
                    self.status = {**self.status, "error": f"{type(error).__name__}: {error}"}
                time.sleep(self.settings.sync_seconds)

        threading.Thread(target=loop, daemon=True, name="proxy-read").start()

    def texts(self, chosen: list[Call]) -> list[tuple[tuple[str, str], ...]]:
        """Each chosen call's parts, read back from the proxy's files by its ``Where``, in the
        order given. Never takes the refresh lock (the first refresh can hold it for the whole
        history) and parses only the chosen lines.

        A line is used only if its SHA-256 is the one indexed, so the text is the text that was
        listed. A call that cannot be found raises ``GoneError``; one that reads differently, or
        whose file no longer decompresses, raises ``ChangedError`` and its file is parsed again
        at the next refresh; a disk error raises ``StoreError``. Never part of an answer."""
        # One listing can miss a pending file and its new .zst while the proxy compresses, and
        # a pending file can go between the listing and the open: a call is gone only if every
        # look says so.
        for _ in range(OPEN_TRIES):
            problems: list[dict] = []
            handle, listing = self._snapshot(problems)
            try:
                plan, missing = _resolve(chosen, handle, listing, self._live_first)
                if not missing:
                    try:
                        found = self._read_lines(plan, handle)
                        break
                    except _Vanished as vanished:
                        missing = vanished.calls
            finally:
                if handle is not None:
                    handle.close()
        else:
            if problems:  # the live file kept moving, or would not open
                raise StoreError("; ".join(f"{p['file']}: {p['problem']}" for p in problems))
            raise GoneError(missing)
        return [self._checked(c, *found[c.where.seq]) for c in chosen]

    def _read_lines(self, plan, handle) -> dict[int, tuple[object, bytes]]:
        """{seq: (where it was read, the line)} for the planned files."""
        found: dict[int, tuple[object, bytes]] = {}
        for where, wanted in plan.items():
            offsets = sorted((c.where.offset, c.where.seq) for c in wanted)
            key = LIVE if where == LIVE else (where.first, where.last)
            name = LIVE if where == LIVE else where.name
            try:
                if where == LIVE:
                    got = _lines_at(handle, False, offsets)
                else:
                    try:
                        f, compressed = _open(where)
                    except FileNotFoundError:
                        raise _Vanished(wanted) from None
                    with f:
                        got = _lines_at(f, compressed, offsets)
            except zstandard.ZstdError as error:
                self._mark_stale(key)
                raise ChangedError(f"{name} no longer reads: {error}") from None
            except OSError as error:  # the disk, not the file: try again later
                raise StoreError(f"{name} cannot be read: {error}") from None
            found.update({seq: (key, line) for seq, line in got.items()})
        return found

    def _checked(self, call: Call, key, line: bytes) -> tuple[tuple[str, str], ...]:
        """The parts of the line read back for ``call``, if it is the line indexed."""
        where = call.where
        again = None
        if line.endswith(b"\n") and hashlib.sha256(line).digest() == where.sha256:
            again = _call(line, where.seq, where.offset)
        if again is None or again.id != call.id:
            self._mark_stale(key)
            raise ChangedError(f"{_named(call)} no longer reads as it did when it was listed")
        return again.parts

    def _mark_stale(self, key) -> None:
        with self._stale_lock:
            self._stale.add(key)

    # ---- one refresh ------------------------------------------------------------------

    def _refresh(self) -> tuple[dict, list[dict]]:
        if not self.dir.is_dir():
            raise StoreError(f"the proxy's directory {self.dir} is missing or not a directory")
        problems: list[dict] = []
        handle, listing = self._snapshot(problems)
        try:
            live_first = listing[-1].last + 1 if listing else 1
            if live_first < self._live_first and self._backwards != live_first:
                # Fewer segments than last time: a listing caught mid-change, or a proxy log
                # that started again. Change nothing until a second look says the same.
                self._backwards = live_first
                problems.append(
                    {
                        "file": SEGMENTS,
                        "problem": f"the stream ends before seq {self._live_first} seen earlier; "
                        "nothing changed, read again next time",
                    }
                )
                return self._result(len(listing), 0), problems
            changed = False
            with self._stale_lock:
                stale, self._stale = self._stale, set()
            for key in stale:  # files a read-back found changed: parse them again
                if key == LIVE:
                    self._live = Live()
                else:
                    self._parsed.pop(key, None)
                changed = True
            # The second look: the proxy's log started again (or its newest segments were
            # removed by hand, which reads the same). Everything is read afresh.
            if live_first < self._live_first:
                self._parsed, self._live, changed = {}, Live(), True
                problems.append({"file": SEGMENTS, "problem": "the proxy's log started again"})
            self._live_first, self._backwards = live_first, None

            new = 0
            for c in listing:
                if (c.first, c.last) in self._parsed:
                    continue
                parsed, problem = _parse(c)
                if parsed is None:  # to be tried again; a cached failure is reported below
                    problems.append({"file": c.name, "problem": problem})
                else:
                    self._parsed[(c.first, c.last)] = parsed
                    new += 1
            changed |= new > 0
            problems += self._file_problems({(c.first, c.last) for c in listing})

            if handle is not None:
                changed |= self._read_live(handle, live_first, problems)
            if self._live.unreadable:
                problems.append(
                    {"file": LIVE, "problem": f"{self._live.unreadable} lines are not calls"}
                )
            if changed:
                self._calls = self._index()
            return self._result(len(listing), new), problems
        finally:
            if handle is not None:
                handle.close()

    def _result(self, segments: int, new: int) -> dict:
        return {
            "new": new,
            "segments": segments,
            "live": self._live.lines,
            "calls": len(self._calls),
        }

    def _snapshot(self, problems: list[dict]):
        """(open live file or None, sealed and pending files), the live file opened first and
        still ``requests.jsonl`` once the listing is made."""
        listing: list[Container] = []
        for _ in range(OPEN_TRIES):
            try:
                handle = (self.dir / LIVE).open("rb")
            except FileNotFoundError:  # between a rotation's two renames, or no log yet
                return None, self._list()
            except OSError as error:
                problems.append({"file": LIVE, "problem": f"cannot be opened: {error}"})
                return None, self._list()
            try:
                listing = self._list()
                if _same_file(handle, self.dir / LIVE):
                    return handle, listing
            except BaseException:
                handle.close()
                raise
            handle.close()
        problems.append({"file": LIVE, "problem": "kept rotating while opened; read next time"})
        return None, listing

    def _list(self) -> list[Container]:
        """The sealed and pending files, by first seq; the ``.zst`` wins over its pending file,
        as in the proxy's ``segments.containers()``."""
        try:
            entries = list(os.scandir(self.dir / SEGMENTS))
        except FileNotFoundError:  # before the proxy's first rotation
            return []
        except OSError as error:
            raise StoreError(f"cannot list {self.dir / SEGMENTS}: {error}") from None
        found: dict[int, Container] = {}
        for entry in entries:
            if m := NAME_RE.match(entry.name):
                first, last, compressed = int(m[1]), int(m[2]), bool(m[3])
                if first not in found or compressed:
                    found[first] = Container(first, last, Path(entry.path), compressed)
        return [found[k] for k in sorted(found)]

    def _file_problems(self, listed: set[tuple[int, int]]) -> list[dict]:
        """Files that failed, lines that are not calls, gaps, and files gone since read
        (their calls are kept: the proxy never deletes a range)."""
        problems: list[dict] = []
        expected = None
        for key in sorted(listed | self._parsed.keys()):
            if expected is not None and key[0] != expected:
                problems.append(
                    {"file": SEGMENTS, "problem": f"no file holds seqs {expected}..{key[0] - 1}"}
                )
            expected = key[1] + 1
            parsed = self._parsed.get(key)
            if parsed is None:
                continue
            if key not in listed:
                problems.append({"file": parsed.name, "problem": "gone; its calls are kept"})
            if parsed.error:
                problems.append({"file": parsed.name, "problem": parsed.error})
            if parsed.unreadable:
                problems.append(
                    {"file": parsed.name, "problem": f"{parsed.unreadable} lines are not calls"}
                )
        return problems

    def _read_live(self, handle, live_first: int, problems: list[dict]) -> bool:
        """Read the live file on from the last offset, whole lines only. True if anything
        changed. A different file (another first seq, or another inode) is read from 0."""
        stat = os.fstat(handle.fileno())
        identity = (live_first, stat.st_dev, stat.st_ino)
        live, changed = self._live, False
        if live.identity != identity or stat.st_size < live.offset:
            live, changed = Live(identity=identity), True
        try:
            handle.seek(live.offset)
            for line in handle:
                if not line.endswith(b"\n"):  # still being written
                    break
                n, at = live.lines, live.offset
                live.offset += len(line)
                live.lines += 1
                changed = True
                call = _call(line, live_first + n, at)
                if call is not None:
                    live.calls.append((call.id or f"{live_first}:{n}", call))
                elif line.strip():
                    live.unreadable += 1
        except OSError as error:
            problems.append({"file": LIVE, "problem": f"read stopped: {error}"})
        self._live = live
        return changed

    def _index(self) -> tuple[Call, ...]:
        """The stream in order: sealed and pending files by seq, then the live file. A call
        id seen twice keeps its first place and its latest content."""
        seen: dict[str, Call] = {}
        for first_last in sorted(self._parsed):
            for key, call in self._parsed[first_last].calls:
                seen[key] = call
        for key, call in self._live.calls:
            seen[key] = call
        return tuple(seen.values())


def _named(call: Call) -> str:
    return f"seq {call.where.seq}" + (f" (call {call.id})" if call.id else "")


def _resolve(chosen: list[Call], handle, listing: list[Container], live_seen: int):
    """({container or LIVE: [calls]}, [calls no file holds]): each call by its seq, in the
    listed file whose name holds it, or the live file past them. ``live_seen`` is the highest
    live first seq the index has seen: a listing missing its newest files must not make an
    older call look live."""
    firsts = [c.first for c in listing]
    live_first = max(listing[-1].last + 1 if listing else 1, live_seen)
    plan: dict[object, list[Call]] = {}
    unresolved: list[Call] = []
    for call in chosen:
        seq = call.where.seq
        i = bisect.bisect_right(firsts, seq) - 1
        if i >= 0 and seq <= listing[i].last:
            plan.setdefault(listing[i], []).append(call)
        elif seq >= live_first and handle is not None:
            plan.setdefault(LIVE, []).append(call)
        else:
            unresolved.append(call)
    return plan, unresolved


def _open(c: Container):
    """(open file, compressed) for a listed file; a pending file compressed meanwhile is read
    from its ``.zst``."""
    try:
        return c.path.open("rb"), c.compressed
    except FileNotFoundError:
        if c.compressed:
            raise
        return c.path.with_name(c.path.name + ".zst").open("rb"), True


def _lines_at(f, compressed: bool, offsets: list[tuple[int, int]]) -> dict[int, bytes]:
    """{seq: the line starting at its offset} in one opened file, offsets in increasing
    order. A compressed file is read forward (as the proxy's segments.read_at does), one at a
    time, and only up to the last wanted line."""
    got: dict[int, bytes] = {}
    if not compressed:
        for offset, seq in offsets:
            f.seek(offset)
            got[seq] = f.readline()
        return got
    with _segment_lock:
        decompressor = zstandard.ZstdDecompressor(max_window_size=1 << WINDOW_LOG)
        reader = io.BufferedReader(decompressor.stream_reader(f, closefd=False))
        at = 0
        for offset, seq in offsets:
            while at < offset:
                skipped = reader.read(min(offset - at, 1 << 20))
                if not skipped:
                    break
                at += len(skipped)
            line = reader.readline() if at == offset else b""
            at += len(line)
            got[seq] = line
    return got


def _same_file(handle, path: Path) -> bool:
    try:
        now = os.stat(path)
    except FileNotFoundError:
        return False
    held = os.fstat(handle.fileno())
    return (held.st_dev, held.st_ino) == (now.st_dev, now.st_ino)


_segment_lock = threading.Lock()  # one sealed segment decompressing at a time: each holds
# the decompressor's window (up to 128 MiB) while it is read


def _parse(c: Container) -> tuple[Parsed | None, str | None]:
    """(what the file held, a problem to report). None for a file to try again next time:
    one gone, or one that could not be opened."""
    path, compressed = c.path, c.compressed
    try:
        f = path.open("rb")
    except FileNotFoundError:
        if compressed:
            return None, "gone before it could be read"
        path, compressed = path.with_name(path.name + ".zst"), True  # compressed meanwhile
        try:
            f = path.open("rb")
        except FileNotFoundError:
            return None, "gone before it could be read"
        except OSError as error:
            return None, f"cannot be opened: {error}"
    except OSError as error:
        return None, f"cannot be opened: {error}"
    name = f"{SEGMENTS}/{path.name}"
    with f:
        try:
            found, lines, unreadable = _read(f, compressed, c.first)
        except (zstandard.ZstdError, OSError) as error:
            failed = Parsed(name, (), 0, f"cannot be read, skipped: {error}")
            return failed, failed.error
    expected = c.last - c.first + 1
    if lines != expected:  # a truncated .zst decompresses without an error
        failed = Parsed(name, (), 0, f"holds {lines} lines, its name says {expected}; skipped")
        return failed, failed.error
    keyed = tuple((call.id or f"{c.first}:{n}", call) for n, call in found)
    return Parsed(name, keyed, unreadable), None


def _read(f, compressed: bool, first: int) -> tuple[list[tuple[int, Call]], int, int]:
    """([(line number, call)], lines, unreadable lines) of one opened file whose first line
    is seq ``first``."""
    if not compressed:
        return _calls(f, first)
    with _segment_lock:
        decompressor = zstandard.ZstdDecompressor(max_window_size=1 << WINDOW_LOG)
        return _calls(io.BufferedReader(decompressor.stream_reader(f, closefd=False)), first)


def _calls(lines, first: int) -> tuple[list[tuple[int, Call]], int, int]:
    found, n, unreadable, offset = [], 0, 0, 0
    for n, line in enumerate(lines, 1):  # noqa: B007 - n is the line count after the loop
        call = _call(line, first + n - 1, offset)
        offset += len(line)
        if call is not None:
            found.append((n - 1, call))
        elif line.strip():
            unreadable += 1
    return found, n, unreadable


def _call(line: bytes, seq: int, offset: int) -> Call | None:
    """One line as a call, with where it is; None for a line that does not parse (skipped,
    never fatal: one odd line, say a request nested past the recursion limit, must not stop
    every read). Every line counts for ``seq``, whatever it holds."""
    if not line.strip():
        return None
    try:
        call = calls.parse(json.loads(line))
    except Exception:
        return None
    return replace(call, where=Where(seq, offset, hashlib.sha256(line).digest()))
