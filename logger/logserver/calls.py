"""One proxied call as the proxy recorded it, read as far as the box needs: who, when, which
session, and the parts of it a run sends to the box.

The reading rules are the agreement UI's (ui/server/proxylogs.py), kept the same on purpose,
so a run reads the same records whichever of the two built them. The UI's copy goes away
when it reads from this server only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime

SESSION_GAP_SECONDS = 30 * 60  # calls from one person this close together form one session
REMINDER = re.compile(r"(<system-reminder>.*?</system-reminder>)", re.DOTALL)
# What one record may carry. Tool results (file contents, command output) and tool inputs
# can be enormous, and a plan allows about 100 KB per run, so they are cut, head first.
CONTENT_LIMITS = {"request": 8000, "response": 8000, "tool_call": 2000, "tool_result": 2000}


@dataclass(frozen=True)
class Call:
    """One call without its conversation: the metadata, and the parts kept for the box."""

    id: str
    time: float
    who: str
    status: int | None
    duration_s: float | None
    session_id: str
    model: str | None
    stop_reason: str | None
    size: int  # bytes of the request body
    message_count: int
    parts: tuple[tuple[str, str], ...]  # (kind, content), in order; never served as metadata

    @property
    def ts(self) -> str:
        return datetime.fromtimestamp(self.time, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def metadata(self) -> dict:
        """What a caller may see without the box: nothing that was said."""
        return {
            "id": self.id,
            "ts": self.ts,
            "who": self.who,
            "status": self.status,
            "duration_s": self.duration_s,
            "model": self.model,
            "stop_reason": self.stop_reason,
            "request_bytes": self.size,
            "messages": self.message_count,
            "records": len(self.parts),
            "kinds": [kind for kind, _ in self.parts],
        }


@dataclass(frozen=True)
class Session:
    id: str
    who: str
    calls: tuple[Call, ...]  # oldest first

    def metadata(self) -> dict:
        return {
            "id": self.id,
            "who": self.who,
            "started": self.calls[0].ts,
            "ended": self.calls[-1].ts,
            "model": next((c.model for c in self.calls if c.model), None),
            "records": sum(len(c.parts) for c in self.calls),
            "calls": [c.metadata() for c in self.calls],
        }


def parse(entry: dict) -> Call:
    body = _json_text(entry.get("request_raw"))
    reply = _json_text(entry.get("response_raw"))
    headers = {k.lower(): v for k, v in (entry.get("request_headers") or {}).items()}
    session = headers.get("x-claude-code-session-id") or ""
    messages: list[dict] = []
    model = None
    if isinstance(body, dict):
        model = body.get("model")
        for m in body.get("messages") or []:
            if isinstance(m, dict):
                messages.append({"role": m.get("role", "?"), "blocks": _blocks(m.get("content"))})
        if not session:
            session = _session_from_metadata(body.get("metadata"))
    response_blocks, stop = _response_blocks(reply)
    raw_req = entry.get("request_raw") or {}
    size = (
        len(raw_req.get("text") or raw_req.get("base64") or "") if isinstance(raw_req, dict) else 0
    )
    return Call(
        id=str(entry.get("id", "")),
        time=float(entry.get("time", 0)),
        who=str(entry.get("who", "?")),
        status=entry.get("status"),
        duration_s=entry.get("duration_s"),
        session_id=session,
        model=model if isinstance(model, str) else None,
        stop_reason=stop,
        size=size,
        message_count=len(messages),
        parts=tuple(_parts(messages, response_blocks)),
    )


def _parts(messages: list[dict], response_blocks: list[dict]) -> list[tuple[str, str]]:
    """(kind, content) pairs for one call, in order: the newest user turn (its text and the
    tool results it carries), then the model's answer (its text and the tool calls it
    makes). Earlier turns went to the box with their own calls, so they are not repeated;
    empty parts, harness reminders and API errors are left out."""
    parts: list[tuple[str, str]] = []
    newest = next((m for m in reversed(messages) if m["role"] == "user"), None)
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
    answer = [b["text"] for b in response_blocks if b["kind"] == "text"]
    if answer:
        parts.append(("response", "\n".join(answer)))
    for b in response_blocks:
        if b["kind"] == "tool_use":
            name = b["label"].removeprefix("tool call: ")
            parts.append(("tool_call", f"{name} {b['text']}"))
    return [(kind, _cut(body, CONTENT_LIMITS[kind])) for kind, body in parts if body.strip()]


def sessions(calls: list[Call]) -> list[Session]:
    """Group calls into sessions: by the Claude Code session id when the call carries one,
    otherwise by who sent it and a 30-minute gap. Newest session first."""
    groups: dict[str, list[Call]] = {}
    last_by_who: dict[str, tuple[str, float]] = {}
    for c in sorted(calls, key=lambda x: x.time):
        key = c.session_id
        if not key:
            prev = last_by_who.get(c.who)
            if prev and c.time - prev[1] <= SESSION_GAP_SECONDS:
                key = prev[0]
            else:
                key = f"{c.who}-{int(c.time)}"
            last_by_who[c.who] = (key, c.time)
        groups.setdefault(key, []).append(replace(c, session_id=key))
    result = [Session(id=k, who=v[0].who, calls=tuple(v)) for k, v in groups.items()]
    result.sort(key=lambda s: s.calls[-1].time, reverse=True)
    return result


def box_records(calls: list[Call]) -> list[dict]:
    """The box's record format, one record per part of each call, oldest call first.

    agent_id is who sent it (the proxy's "who"), session_id the grouping above, content what
    the model saw and said. The box checks these against the plan's input scope."""
    out: list[dict] = []
    for c in sorted(calls, key=lambda x: x.time):
        for kind, content in c.parts:
            out.append(
                {
                    "stream": "proxy",
                    "batch": 0,
                    "offset": len(out),
                    "agent_id": c.who,
                    "session_id": c.session_id,
                    "ts": c.ts,
                    "kind": kind,
                    "content": content,
                }
            )
    return out


def _cut(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n… [cut here; {len(text):,} characters in all]"


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
