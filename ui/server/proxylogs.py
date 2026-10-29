"""The logging proxy's recorded traffic, for the Logs page and the run input picker.

The proxy (Alex's, in a confidential VM) keeps every request and reply that went through
it and offers a small download API. The test proxy takes its shared "tester" key as
x-proxy-key; production takes its reader key as x-log-key and refuses x-proxy-key:

    GET /_logs            listing: sealed segments (name, sha256, first, last, bytes) + live
    GET /_logs/<name>     one sealed segment: zstd-compressed JSON lines, one request each
    GET /_logs/live       the requests not sealed yet, plain JSON lines

Segments are saved under ``settings.logs_dir`` byte for byte and checked against the
sha256 the proxy lists; the live part is replaced on every sync. Downloads stream to disk:
production's live part alone is hundreds of megabytes. A background thread syncs every five
minutes once the server is up (``start_prefetch``) and re-indexes after each sync, so the
page never waits for the proxy.

The pages work from a light index (``load_index``): one ``Request`` per call with the
listing fields, the message count, the first user text, the parts a run sends to the box,
and where the line sits on disk, but no conversation. A call's full conversation is parsed
only when it is opened (``load_full``). Holding every parsed call in memory took about
1 GB for 344 production calls; the service has 512 MB. Everything below that reads
records is tolerant: a record that does not parse is shown raw, never dropped.
"""

from __future__ import annotations

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

from server.config import Settings

SESSION_GAP_SECONDS = 30 * 60  # records from one person this close together form one session
PREFETCH_SECONDS = 300  # how often the background thread asks the proxy for new segments;
# production's live part is re-downloaded whole each time (185 MB on 2026-10-29)
REMINDER = re.compile(r"(<system-reminder>.*?</system-reminder>)", re.DOTALL)


class ProxyError(RuntimeError):
    pass


# ---- download ---------------------------------------------------------------------------


def _get(settings: Settings, path: str) -> tuple[bytes, dict[str, str]]:
    if not settings.proxy_key:
        raise ProxyError("no proxy key configured (AIAB_LOG_KEY or AIAB_PROXY_KEY)")
    request = urllib.request.Request(
        settings.proxy_url + path, headers={settings.proxy_header: settings.proxy_key}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read(), {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as error:
        raise _http_error(error, path) from None
    except (urllib.error.URLError, TimeoutError) as error:
        raise ProxyError(f"the proxy is not answering: {error}") from None


def _download(settings: Settings, path: str, target: Path) -> tuple[str, int, dict[str, str]]:
    """Stream one download to ``target`` (through a .part file, renamed when complete).
    Returns (sha256, newline count, headers)."""
    if not settings.proxy_key:
        raise ProxyError("no proxy key configured (AIAB_LOG_KEY or AIAB_PROXY_KEY)")
    request = urllib.request.Request(
        settings.proxy_url + path, headers={settings.proxy_header: settings.proxy_key}
    )
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


def sync(settings: Settings) -> dict:
    """Fetch the listing, download missing segments, refresh the live part.

    Returns {"new": n, "segments": total, "live_entries": m}.
    """
    out = settings.logs_dir
    out.mkdir(parents=True, exist_ok=True)
    body, _ = _get(settings, "/_logs")
    listing = json.loads(body)
    new = 0
    for seg in listing.get("segments", []):
        name = seg["name"]
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", name):
            continue
        target = out / name
        if target.exists() and _sha256(target) == seg["sha256"]:
            continue
        sha, _, _ = _download(settings, f"/_logs/{name}", target)
        if sha != seg["sha256"]:
            target.unlink()
            raise ProxyError(f"{name}: downloaded bytes do not match the listed sha256")
        new += 1
    _, live_entries, headers = _download(settings, "/_logs/live", out / "live.jsonl")
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


def _decompress(raw: bytes) -> bytes:
    try:
        from compression import zstd  # Python 3.14+

        return zstd.decompress(raw)
    except ImportError:
        import zstandard

        return zstandard.ZstdDecompressor().decompressobj().decompress(raw)


# ---- background prefetch -------------------------------------------------------------------

_sync_lock = threading.Lock()
_state: dict = {"at": None, "error": None, "result": None, "running": False, "started": False}


def sync_now(settings: Settings) -> dict:
    """One sync at a time, whoever asks (the Refresh button or the background thread)."""
    with _sync_lock:
        _state["running"] = True
        try:
            result = sync(settings)
        except ProxyError as exc:
            _state.update(error=str(exc), running=False)
            raise
        _state.update(at=time.time(), error=None, result=result, running=False)
    load_index(settings)  # re-index now, so the next page load finds it ready
    return result


def start_prefetch(settings: Settings, every: int = PREFETCH_SECONDS) -> None:
    """Keep the local copy fresh: sync at start, then every ``every`` seconds, forever."""
    if _state["started"] or not settings.proxy_key:
        return
    _state["started"] = True

    def loop() -> None:
        while True:
            try:
                sync_now(settings)
            except Exception as exc:  # the thread must survive anything the proxy does
                _state.update(error=str(exc), running=False)
            time.sleep(every)

    threading.Thread(target=loop, daemon=True, name="proxy-prefetch").start()


def status() -> dict:
    s = dict(_state)
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
    first_user_text: str = ""  # the first user message with text, for the session title
    parts: list[tuple[str, str]] | None = None  # call_parts, kept by the light index
    source: str = ""  # the file the line is in, and where: set by the light index
    offset: int = -1

    @property
    def when(self) -> str:
        return datetime.fromtimestamp(self.time, UTC).strftime("%Y-%m-%d %H:%M:%S")

    @property
    def when_short(self) -> str:
        return datetime.fromtimestamp(self.time, UTC).strftime("%b %d, %I:%M %p")

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


@dataclass
class Session:
    id: str
    who: str
    requests: list[Request]

    @property
    def title(self) -> str:
        """The first thing the person said, taken from the fullest request: side requests
        (a quota check, a title request) share the session id but are not the conversation."""
        fullest = max(self.requests, key=lambda r: (r.message_count, r.time))
        for r in [fullest, *self.requests]:
            if r.first_user_text:
                return _one_line(r.first_user_text, 80)
        return "(no user text)"

    @property
    def started(self) -> str:
        return self.requests[0].when

    @property
    def started_short(self) -> str:
        return self.requests[0].when_short

    @property
    def model(self) -> str:
        return next((r.model for r in self.requests if r.model), "") or ""


_index_lock = threading.Lock()
_index: dict = {"stamp": None, "requests": [], "by_line": {}}


def _paths(settings: Settings) -> list[Path]:
    out = settings.logs_dir
    if not out.is_dir():
        return []
    return [p for p in sorted(out.glob("*.jsonl.zst")) + [out / "live.jsonl"] if p.is_file()]


def _lines(path: Path):
    """(offset, line) for every line of a log file; offsets into the decompressed text."""
    if path.suffix == ".zst":
        data = _decompress(path.read_bytes())  # a sealed segment is about 1 MB
        offset = 0
        for line in data.splitlines(keepends=True):
            yield offset, line
            offset += len(line)
        return
    with path.open("rb") as f:
        offset = 0
        for line in f:
            yield offset, line
            offset += len(line)


def _read_line(settings: Settings, source: str, offset: int) -> bytes:
    path = settings.logs_dir / source
    if path.suffix == ".zst":
        data = _decompress(path.read_bytes())
        end = data.find(b"\n", offset)
        return data[offset : end if end >= 0 else len(data)]
    with path.open("rb") as f:
        f.seek(offset)
        return f.readline()


def load_index(settings: Settings) -> list[Request]:
    """Every recorded call on disk as a light Request, oldest first, de-duplicated by id
    (a later file wins). Rebuilt when a file changes; a line already indexed is not parsed
    again, since the live part is re-downloaded whole on every sync."""
    with _index_lock:
        paths = _paths(settings)
        stamp = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths)
        if _index["stamp"] == stamp:
            return _index["requests"]
        known, by_line = _index["by_line"], {}
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
                light = replace(light, source=path.name, offset=offset)
                seen[light.id or f"{path.name}:{offset}"] = light
        requests = sorted(seen.values(), key=lambda r: r.time)
        _index.update(stamp=stamp, requests=requests, by_line=by_line)
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


def load_full(settings: Settings, light: Request) -> Request:
    """The call with its whole conversation, parsed from its line on disk."""
    if light.offset < 0:
        return light
    entry = raw_entry(settings, light)
    if entry is None:
        return light
    full = parse_request(entry)
    full.session_id = light.session_id
    return full


def raw_entry(settings: Settings, light: Request) -> dict | None:
    """The call as the proxy recorded it, with ``_source`` naming its file."""
    try:
        entry = json.loads(_read_line(settings, light.source, light.offset))
    except (OSError, ValueError):
        return None
    entry["_source"] = light.source
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
    first_user = next((m["text"] for m in messages if m["role"] == "user" and m["text"]), "")
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
        first_user_text=first_user[:500],
    )


def sessions(requests: list[Request]) -> list[Session]:
    """Group requests into sessions: by the Claude Code session id when present, otherwise
    by who sent them and a 30-minute gap. Newest session first. The requests are copied, not
    changed, so the shared index keeps the session ids as recorded."""
    groups: dict[str, list[Request]] = {}
    last_by_who: dict[str, tuple[str, float]] = {}
    for r in sorted(requests, key=lambda x: x.time):
        key = r.session_id
        if not key:
            prev = last_by_who.get(r.who)
            if prev and r.time - prev[1] <= SESSION_GAP_SECONDS:
                key = prev[0]
            else:
                key = f"{r.who}-{int(r.time)}"
            last_by_who[r.who] = (key, r.time)
        groups.setdefault(key, []).append(replace(r, session_id=key))
    result = [Session(id=k, who=v[0].who, requests=v) for k, v in groups.items()]
    result.sort(key=lambda s: s.requests[-1].time, reverse=True)
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


def to_box_records(requests: list[Request]) -> list[dict]:
    """The box's record format, one record per part of each call (see ``call_parts``).

    agent_id is who sent it (the proxy's "who"), session_id the grouping above, content
    what the model saw and said. The box checks these against the plan's input scope.
    """
    out = []
    for r in sorted(requests, key=lambda x: x.time):
        ts = datetime.fromtimestamp(r.time, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        for kind, content in call_parts(r):
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
