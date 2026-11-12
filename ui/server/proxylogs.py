"""The logging proxy's recorded traffic, for the Logs page and the run input picker.

The proxy (Alex's, in a confidential VM) keeps every request and reply that went through
it and offers a small download API. There are several proxies, one ``LogSource`` each
(``settings.log_sources``): the test proxy takes its shared "tester" key as x-proxy-key;
production takes its reader key as x-log-key and refuses x-proxy-key:

    GET /_logs            listing: sealed segments (name, sha256, first, last, bytes) + live
    GET /_logs/<name>     one sealed segment: zstd-compressed JSON lines, one request each
    GET /_logs/live       the requests not sealed yet, plain JSON lines

Segments are saved under the source's folder (``source.path(settings)``) byte for byte
and checked against the sha256 the proxy lists; the live part is replaced on every sync.
Downloads stream to disk: production's live part alone is hundreds of megabytes. A
background thread per configured source syncs every five minutes once the server is up
(``start_prefetch``) and re-indexes after each sync, so the page never waits for the proxy.
Every function below that reads or fetches takes the source; the sync state, the index and
their locks are kept per source, so a slow production download never holds up the test one.

Slack Logs is a source of another kind: the public-channel export, a static copy that this
server never updates. Nothing is fetched; ``load_index`` hands its files to
``server.slacklogs``, which reads one ``SlackMessage`` per message, grouped into a session
per thread (or per channel and day for messages outside threads). GitHub Logs, the
organisation's public repositories, is read the same way by ``server.githublogs``: one
``GitHubPost`` per issue, pull request, comment, review or commit, a session per thread
(or per repository and day for commits). The session layer below
(``Session``, ``sessions``, ``to_box_records``) takes either kind of record.

The pages work from a light index (``load_index``): one ``Request`` per call with the
listing fields, the message count, the first user text, the parts a run sends to the box,
and where the line sits on disk, but no conversation. A call's full conversation is parsed
only when it is opened (``load_full``). Holding every parsed call in memory took about
1 GB for 344 production calls; the service has 512 MB. Everything below that reads
records is tolerant: a record that does not parse is shown raw, never dropped.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from server import githublogs, plantlogs, slacklogs
from server.config import LogSource, Settings
from server.githublogs import GitHubPost
from server.plantlogs import PlantRecord
from server.slacklogs import SlackMessage

SESSION_GAP_SECONDS = 30 * 60  # records from one person this close together form one session
PREFETCH_SECONDS = 300  # how often the background thread asks the proxy for new segments;
# production's live part is re-downloaded whole each time (185 MB on 2026-10-29)
REMINDER = re.compile(r"(<system-reminder>.*?</system-reminder>)", re.DOTALL)


class ProxyError(RuntimeError):
    pass


# ---- download ---------------------------------------------------------------------------


def _request(source: LogSource, path: str) -> urllib.request.Request:
    if not source.key:
        raise ProxyError(f"no proxy key configured ({source.key_env})")
    return urllib.request.Request(source.url + path, headers={source.header: source.key})


def _get(source: LogSource, path: str) -> tuple[bytes, dict[str, str]]:
    request = _request(source, path)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read(), {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as error:
        raise _http_error(error, path) from None
    except (urllib.error.URLError, TimeoutError) as error:
        raise ProxyError(f"the proxy is not answering: {error}") from None


def _download(source: LogSource, path: str, target: Path) -> tuple[str, int, dict[str, str]]:
    """Stream one download to ``target`` (through a .part file, renamed when complete).
    Returns (sha256, newline count, headers)."""
    request = _request(source, path)
    part = target.with_name(target.name + ".part")
    digest, lines = hashlib.sha256(), 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response, part.open("wb") as out:
            headers = {k.lower(): v for k, v in response.headers.items()}
            while chunk := response.read(1 << 20):
                digest.update(chunk)
                lines += chunk.count(b"\n")
                out.write(chunk)
        os.replace(part, target)
    except urllib.error.HTTPError as error:
        raise _http_error(error, path) from None
    except (urllib.error.URLError, TimeoutError) as error:
        raise ProxyError(f"the proxy is not answering: {error}") from None
    finally:
        part.unlink(missing_ok=True)
    return digest.hexdigest(), lines, headers


def _http_error(error: urllib.error.HTTPError, path: str) -> ProxyError:
    if error.code in (401, 403):
        return ProxyError("the proxy refused the key; it may have changed")
    return ProxyError(f"the proxy answered HTTP {error.code} for {path}")


def sync(settings: Settings, source: LogSource) -> dict:
    """Fetch the listing, download missing segments, refresh the live part.

    Returns {"new": n, "segments": total, "live_entries": m}.
    """
    out = source.path(settings)
    out.mkdir(parents=True, exist_ok=True)
    body, _ = _get(source, "/_logs")
    listing = json.loads(body)
    new = 0
    for seg in listing.get("segments", []):
        name = seg["name"]
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", name):
            continue
        target = out / name
        if target.exists() and _sha256(target) == seg["sha256"]:
            continue
        sha, _, _ = _download(source, f"/_logs/{name}", target)
        if sha != seg["sha256"]:
            target.unlink()
            raise ProxyError(f"{name}: downloaded bytes do not match the listed sha256")
        new += 1
    _, live_entries, headers = _download(source, "/_logs/live", out / "live.jsonl")
    (out / "listing.json").write_text(json.dumps(listing, indent=2) + "\n", encoding="utf-8")
    return {
        "new": new,
        "segments": len(listing.get("segments", [])),
        "live_entries": live_entries,
        "live_first": headers.get("x-first-seq"),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _open_log(path: Path):
    """A log file opened for reading lines, decompressed as it is read when sealed. Sealed
    segments are small on disk but not when decompressed: production's first one was
    2.9 MB holding 190 MB of text, so a segment is never decompressed whole."""
    if path.suffix != ".zst":
        return path.open("rb")
    try:
        from compression import zstd  # Python 3.14+

        return zstd.open(path, "rb")
    except ImportError:
        import io

        import zstandard

        reader = zstandard.ZstdDecompressor().stream_reader(path.open("rb"), closefd=True)
        return io.BufferedReader(reader)


# ---- the old layout --------------------------------------------------------------------

LEGACY_FILES = ("*.jsonl.zst", "live.jsonl", "listing.json")


def adopt_legacy_layout(settings: Settings) -> list[str]:
    """Until 2026-11 the test proxy's segments sat at the root of ``logs_dir`` (production's
    already in ``prod/``). Move them into the Dev Agent Logs folder, once, unless that
    folder already holds segments; ``prod/`` is never touched. Returns the names moved."""
    root = settings.logs_dir
    dev = settings.log_source("dev").path(settings)
    if not root.is_dir() or dev == root:
        return []
    stray = [p for pattern in LEGACY_FILES for p in root.glob(pattern) if p.is_file()]
    settled = [p for pattern in LEGACY_FILES for p in dev.glob(pattern)]
    if not stray or settled:
        return []
    dev.mkdir(parents=True, exist_ok=True)
    moved = []
    for path in stray:
        os.replace(path, dev / path.name)
        moved.append(path.name)
    return moved


# ---- background prefetch -------------------------------------------------------------------

_states_lock = threading.Lock()  # creating a source's state and locks, once each
_states: dict[str, dict] = {}


def _state(source: LogSource) -> dict:
    """This source's sync state and locks: one sync at a time per source, one index."""
    with _states_lock:
        return _states.setdefault(
            source.id,
            {
                "at": None,
                "error": None,
                "result": None,
                "running": False,
                "started": False,
                "sync_lock": threading.Lock(),
                "index_lock": threading.Lock(),
                "index": {"stamp": None, "requests": [], "by_line": {}},
            },
        )


def sync_now(settings: Settings, source: LogSource) -> dict:
    """One sync at a time per source, whoever asks (Refresh or the background thread)."""
    state = _state(source)
    with state["sync_lock"]:
        state["running"] = True
        try:
            result = sync(settings, source)
        except ProxyError as exc:
            state.update(error=str(exc), running=False)
            raise
        state.update(at=time.time(), error=None, result=result, running=False)
    load_index(settings, source)  # re-index now, so the next page load finds it ready
    return result


def start_prefetch(settings: Settings, every: int = PREFETCH_SECONDS) -> None:
    """Keep every configured source's local copy fresh: one thread each, syncing at start
    and then every ``every`` seconds, forever. A source without a key is left alone."""
    for source in settings.log_sources:
        state = _state(source)
        if state["started"] or not source.fetches:
            continue
        state["started"] = True

        def loop(source: LogSource = source, state: dict = state) -> None:
            while True:
                try:
                    sync_now(settings, source)
                except Exception as exc:  # the thread must survive anything the proxy does
                    state.update(error=str(exc), running=False)
                time.sleep(every)

        threading.Thread(target=loop, daemon=True, name=f"proxy-prefetch-{source.id}").start()


def status(source: LogSource) -> dict:
    state = _state(source)
    s = {k: v for k, v in state.items() if k in ("at", "error", "result", "running", "started")}
    s["ago"] = int(time.time() - s["at"]) if s["at"] else None
    s["every"] = PREFETCH_SECONDS
    return s


# ---- records and sessions --------------------------------------------------------------


@dataclass
class Request:
    """One proxied call, parsed as far as it goes."""

    id: str
    time: float
    who: str
    status: int | None
    duration_s: float | None
    session_id: str
    model: str | None
    system_text: str
    tool_names: list[str]
    messages: list[dict]  # [{"role", "text", "blocks": [{"kind", "label", "text"}]}]
    response_blocks: list[dict]  # the assistant's answer, in the same block form
    stop_reason: str | None
    size: int  # bytes of the request body
    raw: dict = field(repr=False)
    message_count: int = 0
    parts: list[tuple[str, str]] | None = None  # call_parts, kept by the light index
    file: str = ""  # the file the line is in, and where: set by the light index
    offset: int = -1

    @property
    def when(self) -> str:
        return datetime.fromtimestamp(self.time, UTC).strftime("%Y-%m-%d %H:%M:%S")

    @property
    def when_short(self) -> str:
        return datetime.fromtimestamp(self.time, UTC).strftime("%b %d, %I:%M %p")

    @property
    def title_text(self) -> str:
        return ""  # a proxied call never lends its text to a title (MAC-377)

    @property
    def last_user_text(self) -> str:
        for m in reversed(self.messages):
            if m["role"] == "user" and m["text"]:
                return m["text"]
        return ""

    @property
    def response_text(self) -> str:
        return "\n".join(b["text"] for b in self.response_blocks if b["kind"] in ("text", "error"))

    @property
    def size_kb(self) -> str:
        return f"{self.size / 1024:.1f} KB"


Record = Request | SlackMessage | GitHubPost | PlantRecord  # calls, posts or plant records


@dataclass
class Session:
    id: str
    who: str
    records: list[Record]  # oldest first

    @property
    def title(self) -> str:
        """A proxied session is titled by who, when and how many calls, never by what was
        said: the text is for the box, not for this server's lists (MAC-377). A Slack
        or GitHub session is titled the same way."""
        n = len(self.records)
        if isinstance(self.records[0], Request):
            return f"{self.who} · {self.started_short} · {n} call{'s' if n != 1 else ''}"
        if isinstance(self.records[0], PlantRecord):
            return f"{self.who} · {self.started_short} · {n} record{'s' if n != 1 else ''}"
        if isinstance(self.records[0], SlackMessage):
            # Slack is shown by metadata only as well: the channel, when, how many messages.
            channel = self.records[0].channel
            return f"#{channel} · {self.started_short} · {n} message{'s' if n != 1 else ''}"
        # GitHub likewise: the thread or the day of commits (the session id), and how many posts.
        return f"{self.id} · {n} post{'s' if n != 1 else ''}"

    @property
    def started(self) -> str:
        return self.records[0].when

    @property
    def started_short(self) -> str:
        return self.records[0].when_short

    @property
    def model(self) -> str:
        return next((r.model for r in self.records if isinstance(r, Request) and r.model), "")


def _paths(settings: Settings, source: LogSource) -> list[Path]:
    out = source.path(settings)
    if not out.is_dir():
        return []
    if source.kind == "plant":
        return plantlogs.record_files(out)
    if source.chat:
        return sorted(p for p in out.glob("*.jsonl") if p.is_file())
    return [p for p in sorted(out.glob("*.jsonl.zst")) + [out / "live.jsonl"] if p.is_file()]


_segment_lock = threading.Lock()  # one sealed segment decompressing at a time: each holds
# the compressor's window (about 130 MB for production's segments) while it is read


def _lines(path: Path):
    """(offset, line) for every line of a log file; offsets into the decompressed text."""
    with _segment_lock if path.suffix == ".zst" else contextlib.nullcontext(), _open_log(path) as f:
        offset = 0
        for line in f:
            yield offset, line
            offset += len(line)


def _read_line(settings: Settings, source: LogSource, file: str, offset: int) -> bytes:
    path = source.path(settings) / file
    if path.suffix != ".zst":
        with path.open("rb") as f:
            f.seek(offset)
            return f.readline()
    for at, line in _lines(path):  # a compressed stream can only be read forward
        if at == offset:
            return line
    return b""


def load_index(settings: Settings, source: LogSource) -> list[Record]:
    """Every recorded call of one source on disk as a light Request, oldest first,
    de-duplicated by id (a later file wins). Rebuilt when a file changes; a line already
    indexed is not parsed again, since the live part is re-downloaded whole on every sync.
    A Slack or GitHub source gives its posts instead (``slacklogs``, ``githublogs``)."""
    state = _state(source)
    with state["index_lock"]:
        index = state["index"]
        paths = _paths(settings, source)
        stamp = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths)
        if index["stamp"] == stamp:
            return index["requests"]
        if source.kind == "slack":
            messages = slacklogs.load_index(paths)
            index.update(stamp=stamp, requests=messages, by_line={})
            return messages
        if source.kind == "plant":
            records = plantlogs.load_index(source.path(settings), paths)
            index.update(stamp=stamp, requests=records, by_line={})
            return records
        if source.kind == "github":
            posts = githublogs.load_index(paths)
            index.update(stamp=stamp, requests=posts, by_line={})
            return posts
        known, by_line = index["by_line"], {}
        seen: dict[str, Request] = {}
        for path in paths:
            for offset, line in _lines(path):
                if not line.strip():
                    continue
                key = hashlib.blake2b(line, digest_size=16).digest()
                light = known.get(key) or by_line.get(key)
                if light is None:
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    light = _light(parse_request(entry))
                by_line[key] = light
                light = replace(light, file=path.name, offset=offset)
                seen[light.id or f"{path.name}:{offset}"] = light
        requests = sorted(seen.values(), key=lambda r: r.time)
        index.update(stamp=stamp, requests=requests, by_line=by_line)
        return requests


def _light(r: Request) -> Request:
    """The index's copy of a call: what the listings and runs use, without the conversation."""
    return replace(
        r,
        system_text="",
        tool_names=[],
        messages=[],
        response_blocks=[],
        raw={},
        parts=call_parts(r),
    )


def load_full(settings: Settings, source: LogSource, light: Record) -> Record:
    """The call with its whole conversation, parsed from its line on disk. A Slack message
    is whole already."""
    if not isinstance(light, Request) or light.offset < 0:
        return light
    entry = raw_entry(settings, source, light)
    if entry is None:
        return light
    full = parse_request(entry)
    full.session_id = light.session_id
    return full


def raw_entry(settings: Settings, source: LogSource, light: Record) -> dict | None:
    """The call as the proxy recorded it, with ``_file`` naming its file; None for a Slack
    message, which has no recorded form beyond its fields."""
    if not isinstance(light, Request):
        return None
    try:
        entry = json.loads(_read_line(settings, source, light.file, light.offset))
    except (OSError, ValueError):
        return None
    entry["_file"] = light.file
    return entry


def parse_request(entry: dict) -> Request:
    body = _json_text(entry.get("request_raw"))
    reply = _json_text(entry.get("response_raw"))
    headers = {k.lower(): v for k, v in (entry.get("request_headers") or {}).items()}
    session = headers.get("x-claude-code-session-id") or ""
    messages: list[dict] = []
    model = None
    system_text = ""
    tool_names: list[str] = []
    if isinstance(body, dict):
        model = body.get("model")
        system_text = _content_text(body.get("system"))
        tool_names = [
            str(t.get("name", "?")) for t in body.get("tools") or [] if isinstance(t, dict)
        ]
        for m in body.get("messages") or []:
            if isinstance(m, dict):
                blocks = _blocks(m.get("content"))
                text = "\n".join(b["text"] for b in blocks if b["kind"] == "text")
                messages.append({"role": m.get("role", "?"), "text": text, "blocks": blocks})
        if not session:
            session = _session_from_metadata(body.get("metadata"))
    response_blocks, stop = _response_blocks(reply)
    raw_req = entry.get("request_raw") or {}
    size = (
        len(raw_req.get("text") or raw_req.get("base64") or "") if isinstance(raw_req, dict) else 0
    )
    return Request(
        id=str(entry.get("id", "")),
        time=float(entry.get("time", 0)),
        who=str(entry.get("who", "?")),
        status=entry.get("status"),
        duration_s=entry.get("duration_s"),
        session_id=session,
        model=model,
        system_text=system_text,
        tool_names=tool_names,
        messages=messages,
        response_blocks=response_blocks,
        stop_reason=stop,
        size=size,
        raw=entry,
        message_count=len(messages),
    )


def sessions(records: list[Record]) -> list[Session]:
    """Group records into sessions: by the session id when present (the Claude Code session,
    a Slack thread or channel day), otherwise by who sent them and a 30-minute gap. Newest
    session first. The records are copied, not changed, so the shared index keeps the
    session ids as recorded."""
    groups: dict[str, list[Record]] = {}
    last_by_who: dict[str, tuple[str, float]] = {}
    for r in sorted(records, key=lambda x: x.time):
        key = r.session_id
        if not key:
            prev = last_by_who.get(r.who)
            if prev and r.time - prev[1] <= SESSION_GAP_SECONDS:
                key = prev[0]
            else:
                key = f"{r.who}-{int(r.time)}"
            last_by_who[r.who] = (key, r.time)
        groups.setdefault(key, []).append(replace(r, session_id=key))
    result = [Session(id=k, who=v[0].who, records=v) for k, v in groups.items()]
    result.sort(key=lambda s: s.records[-1].time, reverse=True)
    return result


# ---- box records, for a run --------------------------------------------------------------


# What one record may carry. Tool results (file contents, command output) and tool inputs
# can be enormous, and the plan allows 100 KB per run, so they are cut, head first.
CONTENT_LIMITS = {"request": 8000, "response": 8000, "tool_call": 2000, "tool_result": 2000}


def _cut(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n… [cut here; {len(text):,} characters in all]"


def call_parts(r: Request) -> list[tuple[str, str]]:
    """(kind, content) pairs for one call, in order: the newest user turn (its text and the
    tool results it carries), then the model's answer (its text and the tool calls it
    makes). Earlier turns went to the box with their own calls, so they are not repeated;
    empty parts, harness reminders and API errors are left out."""
    if r.parts is not None:
        return r.parts
    parts: list[tuple[str, str]] = []
    newest = next((m for m in reversed(r.messages) if m["role"] == "user"), None)
    text: list[str] = []
    for b in newest["blocks"] if newest else []:
        if b["kind"] == "text":
            text.append(b["text"])
        elif b["kind"] == "tool_result":
            if text:
                parts.append(("request", "\n".join(text)))
                text = []
            parts.append(("tool_result", b["text"]))
    if text:
        parts.append(("request", "\n".join(text)))
    answer = [b["text"] for b in r.response_blocks if b["kind"] == "text"]
    if answer:
        parts.append(("response", "\n".join(answer)))
    for b in r.response_blocks:
        if b["kind"] == "tool_use":
            name = b["label"].removeprefix("tool call: ")
            parts.append(("tool_call", f"{name} {b['text']}"))
    return [(kind, _cut(body, CONTENT_LIMITS[kind])) for kind, body in parts if body.strip()]


def box_records(records: list[Record]) -> list[dict]:
    """What a run sends for these records: a plant's records as the plant wrote them (their
    batch set per file, see ``plantlogs``), anything else through ``to_box_records``."""
    if records and all(isinstance(r, PlantRecord) for r in records):
        return [r.box for r in sorted(records, key=lambda r: (r.box["batch"], r.box["offset"]))]
    return to_box_records(records)


def to_box_records(records: list[Record]) -> list[dict]:
    """The box's record format, one record per part of each call (see ``call_parts``), or
    one per Slack message or GitHub post (its text, as a request).

    agent_id is who sent it (the proxy's "who", the message's author), session_id the
    grouping above, content what the model saw and said, or what the person wrote. The box
    checks these against the plan's input scope.
    """
    out = []
    for r in sorted(records, key=lambda x: x.time):
        ts = datetime.fromtimestamp(r.time, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        parts = call_parts(r) if isinstance(r, Request) else r.parts
        for kind, content in parts:
            content = _cut(content, CONTENT_LIMITS[kind])
            out.append(
                {
                    "stream": "proxy",
                    "batch": 0,
                    "offset": len(out),
                    "agent_id": r.who,
                    "session_id": r.session_id,
                    "ts": ts,
                    "kind": kind,
                    "content": content,
                }
            )
    return out


# ---- helpers -----------------------------------------------------------------------------


def _json_text(raw) -> object:
    """request_raw / response_raw hold {"text": ...} or {"base64": ...}; parse JSON if it is."""
    if not isinstance(raw, dict):
        return None
    text = raw.get("text")
    if text is None and raw.get("base64"):
        import base64

        try:
            text = base64.b64decode(raw["base64"]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
    if not isinstance(text, str):
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text  # SSE stream or plain text


def _content_text(content) -> str:
    """Flat text of a message or system field: the blocks' text, tool calls named."""
    return "\n".join(
        b["text"] if b["kind"] == "text" else f"[{b['label']}]" for b in _blocks(content)
    )


def _blocks(content) -> list[dict]:
    """Content as blocks the page can fold: text, reminder, tool_use, tool_result, thinking."""
    if isinstance(content, str):
        return _text_blocks(content)
    out: list[dict] = []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            out.extend(_text_blocks(block.get("text", "")))
        elif kind == "tool_use":
            out.append(
                {
                    "kind": "tool_use",
                    "label": f"tool call: {block.get('name', '?')}",
                    "text": json.dumps(block.get("input"), indent=2, ensure_ascii=False),
                }
            )
        elif kind == "tool_result":
            inner = _content_text(block.get("content"))
            out.append(
                {
                    "kind": "tool_result",
                    "label": f"tool result · {len(inner):,} chars",
                    "text": inner,
                }
            )
        elif kind in ("thinking", "redacted_thinking"):
            out.append(_thinking(block.get("thinking", "")))
        else:
            out.append({"kind": "other", "label": str(kind), "text": json.dumps(block)[:4000]})
    return out


def _thinking(text: str) -> dict:
    """A thinking block. Empty means the API returned only a signature: with
    ``thinking.display`` set to updates or omitted, the reasoning itself is never sent
    back, so it cannot be in the log."""
    if text.strip():
        return {"kind": "thinking", "label": "thinking", "text": text}
    return {
        "kind": "thinking",
        "label": "thinking · not returned by the API, so not in the log",
        "text": "",
    }


def _text_blocks(text: str) -> list[dict]:
    out = []
    for part in REMINDER.split(text):
        part = part.strip()
        if not part:
            continue
        if part.startswith("<system-reminder>"):
            body = part[len("<system-reminder>") : -len("</system-reminder>")].strip()
            out.append(
                {
                    "kind": "reminder",
                    "label": "system reminder: " + _one_line(body, 160),
                    "text": body,
                }
            )
        else:
            out.append({"kind": "text", "label": "", "text": part})
    return out


def _response_blocks(reply) -> tuple[list[dict], str | None]:
    if isinstance(reply, dict):
        if reply.get("type") == "error":
            err = reply.get("error") or {}
            text = f"error: {err.get('type', '?')}: {err.get('message', '')}"
            return [{"kind": "error", "label": "error", "text": text}], "error"
        return _blocks(reply.get("content")), reply.get("stop_reason")
    if isinstance(reply, str) and "event:" in reply:
        return _sse_blocks(reply)
    if isinstance(reply, str) and reply.strip():
        return [{"kind": "text", "label": "", "text": reply}], None
    return [], None


def _sse_blocks(reply: str) -> tuple[list[dict], str | None]:
    """A streamed reply: put the deltas back together, block by block."""
    blocks: dict[int, dict] = {}
    stop = None
    for line in reply.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except ValueError:
            continue
        kind = ev.get("type")
        if kind == "content_block_start":
            start = ev.get("content_block") or {}
            blocks[ev.get("index", len(blocks))] = {
                "type": start.get("type"),
                "name": start.get("name"),
                "text": start.get("text", ""),
                "json": "",
            }
        elif kind == "content_block_delta":
            delta = ev.get("delta") or {}
            b = blocks.setdefault(ev.get("index", 0), {"type": "text", "text": "", "json": ""})
            if delta.get("type") == "text_delta":
                b["text"] += delta.get("text", "")
            elif delta.get("type") == "thinking_delta":
                b["type"] = "thinking"
                b["text"] += delta.get("thinking", "")
            elif delta.get("type") == "input_json_delta":
                b["json"] += delta.get("partial_json", "")
        elif kind == "message_delta":
            stop = (ev.get("delta") or {}).get("stop_reason") or stop
        elif kind == "error":
            err = ev.get("error") or {}
            blocks[len(blocks) + 1000] = {
                "type": "error",
                "text": f"error: {err.get('type', '?')}: {err.get('message', '')}",
                "json": "",
            }
            stop = "error"
    out = []
    for _, b in sorted(blocks.items()):
        if b["type"] == "tool_use":
            try:
                pretty = json.dumps(json.loads(b["json"] or "{}"), indent=2, ensure_ascii=False)
            except ValueError:
                pretty = b["json"]
            out.append(
                {"kind": "tool_use", "label": f"tool call: {b.get('name') or '?'}", "text": pretty}
            )
        elif b["type"] in ("thinking", "redacted_thinking"):
            out.append(_thinking(b["text"]))
        elif b["type"] == "error":
            out.append({"kind": "error", "label": "error", "text": b["text"]})
        elif b["text"].strip():
            out.append({"kind": "text", "label": "", "text": b["text"]})
    return out, stop


def _session_from_metadata(metadata) -> str:
    if isinstance(metadata, dict) and isinstance(metadata.get("user_id"), str):
        try:
            return str(json.loads(metadata["user_id"]).get("session_id") or "")
        except ValueError:
            return ""
    return ""


def _one_line(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
