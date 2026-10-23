"""The logging proxy's recorded traffic, for the Logs page and the run input picker.

The proxy (Alex's, in a confidential VM) keeps every request and reply that went through
it and offers a small download API behind the shared "tester" key:

    GET /_logs            listing: sealed segments (name, sha256, first, last, bytes) + live
    GET /_logs/<name>     one sealed segment: zstd-compressed JSON lines, one request each
    GET /_logs/live       the requests not sealed yet, plain JSON lines

Segments are saved under ``settings.logs_dir`` byte for byte and checked against the
sha256 the proxy lists; the live part is replaced on every sync. A background thread
syncs every minute once the server is up (``start_prefetch``), so the page never waits
for the proxy. Everything below that reads records is tolerant: a record that does not
parse is shown raw, never dropped.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from server.config import Settings

SESSION_GAP_SECONDS = 30 * 60  # records from one person this close together form one session
PREFETCH_SECONDS = 60  # how often the background thread asks the proxy for new segments
REMINDER = re.compile(r"(<system-reminder>.*?</system-reminder>)", re.DOTALL)


class ProxyError(RuntimeError):
    pass


# ---- download ---------------------------------------------------------------------------


def _get(settings: Settings, path: str) -> tuple[bytes, dict[str, str]]:
    if not settings.proxy_key:
        raise ProxyError("no proxy key configured (AIAB_PROXY_KEY)")
    request = urllib.request.Request(
        settings.proxy_url + path, headers={"x-proxy-key": settings.proxy_key}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read(), {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as error:
        if error.code == 401:
            raise ProxyError("the proxy refused the key; it may have changed") from None
        raise ProxyError(f"the proxy answered HTTP {error.code} for {path}") from None
    except (urllib.error.URLError, TimeoutError) as error:
        raise ProxyError(f"the proxy is not answering: {error}") from None


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
        raw, _ = _get(settings, f"/_logs/{name}")
        if hashlib.sha256(raw).hexdigest() != seg["sha256"]:
            raise ProxyError(f"{name}: downloaded bytes do not match the listed sha256")
        target.write_bytes(raw)
        new += 1
    live, headers = _get(settings, "/_logs/live")
    (out / "live.jsonl").write_bytes(live)
    (out / "listing.json").write_text(json.dumps(listing, indent=2) + "\n", encoding="utf-8")
    return {
        "new": new,
        "segments": len(listing.get("segments", [])),
        "live_entries": live.count(b"\n"),
        "live_first": headers.get("x-first-seq"),
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
        fullest = max(self.requests, key=lambda r: (len(r.messages), r.time))
        for r in [fullest, *self.requests]:
            for m in r.messages:
                if m["role"] == "user" and m["text"]:
                    return _one_line(m["text"], 80)
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


_cache: tuple[tuple, list[dict]] | None = None


def load_records(settings: Settings) -> list[dict]:
    """Every recorded request on disk, oldest first, one dict per request, de-duplicated."""
    global _cache
    out = settings.logs_dir
    if not out.is_dir():
        return []
    paths = [p for p in sorted(out.glob("*.jsonl.zst")) + [out / "live.jsonl"] if p.is_file()]
    stamp = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in paths)
    if _cache and _cache[0] == stamp:
        return _cache[1]
    seen: dict[str, dict] = {}
    for path in paths:
        data = _decompress(path.read_bytes()) if path.suffix == ".zst" else path.read_bytes()
        for line in data.splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            entry["_source"] = path.name
            seen[str(entry.get("id") or len(seen))] = entry
    records = sorted(seen.values(), key=lambda e: e.get("time", 0))
    _cache = (stamp, records)
    return records


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
    )


def sessions(records: list[dict]) -> list[Session]:
    """Group requests into sessions: by the Claude Code session id when present, otherwise
    by who sent them and a 30-minute gap. Newest session first."""
    parsed = [parse_request(e) for e in records]
    groups: dict[str, list[Request]] = {}
    last_by_who: dict[str, tuple[str, float]] = {}
    for r in parsed:
        key = r.session_id
        if not key:
            prev = last_by_who.get(r.who)
            if prev and r.time - prev[1] <= SESSION_GAP_SECONDS:
                key = prev[0]
            else:
                key = f"{r.who}-{int(r.time)}"
            last_by_who[r.who] = (key, r.time)
        r.session_id = key
        groups.setdefault(key, []).append(r)
    result = [Session(id=k, who=v[0].who, requests=v) for k, v in groups.items()]
    result.sort(key=lambda s: s.requests[-1].time, reverse=True)
    return result


# ---- box records, for a run --------------------------------------------------------------


def to_box_records(requests: list[Request]) -> list[dict]:
    """The box's record format: one request record and one response record per call.

    agent_id is who sent it (the proxy's "who"), session_id the grouping above, content the
    text the model saw and said. The box checks these against the plan's input scope.
    """
    out = []
    for r in sorted(requests, key=lambda x: x.time):
        ts = datetime.fromtimestamp(r.time, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        base = {
            "stream": "proxy",
            "batch": 0,
            "agent_id": r.who,
            "session_id": r.session_id,
            "ts": ts,
        }
        out.append(
            {
                **base,
                "offset": len(out),
                "kind": "request",
                "content": r.last_user_text or "(no text)",
            }
        )
        out.append(
            {
                **base,
                "offset": len(out),
                "kind": "response",
                "content": r.response_text or "(no text)",
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
