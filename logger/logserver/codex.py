"""The reader for Codex's calls (OpenAI's Responses API), next to the Claude reader in calls.py.

Codex sends POST <prefix>/responses with the whole conversation as ``input`` items and gets a
stream of ``response.*`` events. Its request body can arrive zstd-compressed, and the proxy
stores the bytes as they came. This module unpacks the body and reads both sides into the
same parts the Claude reader gives (request, tool_result, response, tool_call), so sealing,
the export and the agreement UI treat the two alike.

The reading rules follow the logging proxy's own (its transcript.py, MAC-302), so this server
and the proxy's viewer agree on what a Codex call holds.
"""

from __future__ import annotations

import base64
import gzip
import io
import json

import zstandard

# The proxy's two Codex path prefixes (its proxy.CODEX_UPSTREAMS).
PREFIXES = ("/chatgpt/backend-api/codex/", "/openai/v1/")
# The most an unpacked body may be. The proxy stores no line above 128 MiB; a body that
# unpacks to more than this is not read, so one call cannot take the server's memory.
MAX_BODY_BYTES = 256 * 1024 * 1024

Part = tuple[str, str]  # (kind, content)


def is_codex(entry: dict) -> bool:
    return str(entry.get("path") or "").startswith(PREFIXES)


def body(raw, encoding: str = "") -> object:
    """A stored body ({"text": ...} or {"base64": ...}) as it was before the client compressed
    it: parsed JSON if it is JSON, else its text (a stream of events), else None."""
    if not isinstance(raw, dict):
        return None
    if isinstance(raw.get("text"), str):
        data = raw["text"].encode()
    elif raw.get("base64"):
        try:
            data = base64.b64decode(raw["base64"])
        except ValueError:
            return None
    else:
        return None
    try:
        text = _unpacked(data, encoding.strip().lower()).decode("utf-8")
    except (UnicodeDecodeError, zstandard.ZstdError, OSError, EOFError, ValueError):
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


def _unpacked(data: bytes, encoding: str) -> bytes:
    if encoding == "zstd":
        reader = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data))
    elif encoding == "gzip":
        reader = gzip.GzipFile(fileobj=io.BytesIO(data))
    else:
        return data
    out = reader.read(MAX_BODY_BYTES + 1)
    if len(out) > MAX_BODY_BYTES:
        raise ValueError("body too large")
    return out


def read(entry: dict) -> dict:
    """One Codex call: its session, model, how many messages it carried, how it ended and the
    parts a run sends. A call that is no model request (a model list, say) has no parts."""
    headers = {k.lower(): v for k, v in (entry.get("request_headers") or {}).items()}
    request = body(entry.get("request_raw"), str(headers.get("content-encoding") or ""))
    reply = body(entry.get("response_raw"))
    turn = (
        str(entry.get("path") or "").endswith("/responses")
        and isinstance(request, dict)
        and isinstance(request.get("input"), list)
    )
    messages = _messages(request["input"]) if turn else []
    blocks, stop = _reply(reply) if turn else ([], None)
    model = request.get("model") if isinstance(request, dict) else None
    return {
        "session": _session(headers, request if isinstance(request, dict) else {}),
        "model": model if isinstance(model, str) else None,
        "message_count": len(messages),
        "stop_reason": stop,
        "parts": _parts(messages, blocks),
    }


def _parts(messages: list[dict], reply: list[dict]) -> list[Part]:
    """As the Claude reader: the newest user turn (what was typed and the tool results it
    carries), then the model's answer (its text and the tool calls it makes). Earlier turns
    went with their own calls. Context Codex adds itself, reasoning and errors are left out."""
    parts: list[Part] = []
    newest = next((m for m in reversed(messages) if m["role"] == "user"), None)
    text: list[str] = []
    for b in newest["blocks"] if newest else []:
        if b["kind"] == "text" and not b.get("injected"):
            text.append(b["text"])
        elif b["kind"] == "tool_result":
            if text:
                parts.append(("request", "\n".join(text)))
                text = []
            parts.append(("tool_result", b["text"]))
    if text:
        parts.append(("request", "\n".join(text)))
    answer = [b["text"] for b in reply if b["kind"] == "text"]
    if answer:
        parts.append(("response", "\n".join(answer)))
    parts += [("tool_call", f"{b['name']} {b['text']}") for b in reply if b["kind"] == "tool_use"]
    return [(kind, content.strip()) for kind, content in parts if content.strip()]


def _messages(items: list) -> list[dict]:
    """The conversation as messages: tool calls under the assistant, tool results under the
    user, neighbouring items of one role run together. Developer and system messages are the
    instructions, not the conversation, and are left out."""
    messages: list[dict] = []
    for item in items:
        if not isinstance(item, dict) or item.get("type") == "additional_tools":
            continue
        role = item.get("role")
        if role in ("developer", "system"):
            continue
        if role is None:
            role = "user" if str(item.get("type") or "").endswith("_output") else "assistant"
        blocks = _item_blocks(item)
        if messages and messages[-1]["role"] == role:
            messages[-1]["blocks"] += blocks
        else:
            messages.append({"role": role, "blocks": blocks})
    return messages


def _item_blocks(item: dict) -> list[dict]:
    """One input or output item as blocks: text, tool_use, tool_result, thinking or other."""
    kind = item.get("type")
    if kind == "message":
        return _content_blocks(item.get("content"))
    if kind in ("function_call", "custom_tool_call", "local_shell_call"):
        raw = item.get("arguments") if kind == "function_call" else item.get("input")
        if raw is None and kind != "function_call":
            raw = item.get("action")
        if kind == "function_call" and isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                pass  # arguments cut off mid-stream: keep what arrived
        name = str(item.get("name") or kind)
        if item.get("namespace"):
            name = f"{item['namespace']}.{name}"
        text = raw if isinstance(raw, str) else json.dumps(raw, indent=2, ensure_ascii=False)
        return [{"kind": "tool_use", "name": name, "text": text}]
    if kind in ("function_call_output", "custom_tool_call_output", "local_shell_call_output"):
        output = item.get("output", "")
        if not isinstance(output, str):
            output = "\n".join(b["text"] for b in _content_blocks(output) if b["kind"] == "text")
        return [{"kind": "tool_result", "text": output}]
    if kind == "reasoning":
        return [{"kind": "thinking", "text": ""}]  # the reasoning itself is encrypted
    return [{"kind": "other", "text": ""}]  # web_search_call, compaction, ...


def _content_blocks(content) -> list[dict]:
    if isinstance(content, str):
        content = [{"type": "input_text", "text": content}]
    blocks: list[dict] = []
    for part in content if isinstance(content, list) else []:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind in ("input_text", "output_text", "text"):
            text = str(part.get("text") or "")
            blocks.append({"kind": "text", "text": text, "injected": _injected(text)})
        elif kind == "refusal":
            blocks.append({"kind": "text", "text": "[refusal] " + str(part.get("refusal") or "")})
        else:
            blocks.append({"kind": "other", "text": ""})  # input_image, input_file, ...
    return blocks


def _injected(text: str) -> bool:
    """Context Codex adds to user messages (<environment_context>, AGENTS.md), not typed."""
    text = text.strip()
    return (text.startswith("<") and text.endswith(">")) or text.startswith(
        "# AGENTS.md instructions"
    )


def _session(headers: dict, request: dict) -> str:
    """Codex's thread id: its thread-id header (also session-id), else the body's own."""
    metadata = request.get("client_metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    found = (
        headers.get("thread-id")
        or headers.get("session-id")
        or metadata.get("thread_id")
        or metadata.get("session_id")
    )
    return found if isinstance(found, str) else ""


def _events(reply: str):
    for line in reply.splitlines():
        if line.startswith("data:"):
            try:
                event = json.loads(line[5:].strip())
            except ValueError:
                continue
            if isinstance(event, dict):
                yield event


def _reply(reply) -> tuple[list[dict], str | None]:
    """The model's output items as blocks, and how the reply ended, from a response object or
    a stream. In a stream an item is announced, filled by deltas and finished; a finished item
    is taken as sent, an unfinished one (the stream was cut off) keeps what arrived."""
    if isinstance(reply, dict):
        if reply.get("object") != "response" and isinstance(reply.get("error"), dict):
            return [], "error"
        output = reply.get("output")
        items = [i for i in output if isinstance(i, dict)] if isinstance(output, list) else []
        return [b for i in items for b in _item_blocks(i)], _ending(reply)
    if not isinstance(reply, str):
        return [], None
    items_at: dict[int, dict] = {}
    stop = None
    for event in _events(reply):
        kind, index = event.get("type"), event.get("output_index")
        index = index if isinstance(index, int) else -1
        if kind in ("response.output_item.added", "response.output_item.done"):
            if isinstance(event.get("item"), dict):
                items_at[index] = event["item"]
        elif kind == "response.output_text.delta" and index in items_at:
            at = event.get("content_index")
            content = items_at[index].setdefault("content", [])
            if isinstance(at, int) and 0 <= at < 1000 and isinstance(content, list):
                while len(content) <= at:
                    content.append({"type": "output_text", "text": ""})
                if isinstance(content[at], dict):
                    content[at]["text"] = str(content[at].get("text") or "") + str(
                        event.get("delta") or ""
                    )
        elif kind in (
            "response.function_call_arguments.delta",
            "response.custom_tool_call_input.delta",
        ):
            if index in items_at:
                field = "arguments" if kind.startswith("response.function") else "input"
                items_at[index][field] = str(items_at[index].get(field) or "") + str(
                    event.get("delta") or ""
                )
        elif kind in ("response.completed", "response.incomplete", "response.failed"):
            response = event.get("response")
            stop = _ending(response if isinstance(response, dict) else {}) or kind[9:]
        elif kind == "error":
            stop = "error"
    return [b for _, item in sorted(items_at.items()) for b in _item_blocks(item)], stop


def _ending(response: dict) -> str | None:
    """How a response ended: completed, the reason it is incomplete, or error."""
    status = response.get("status")
    if status == "failed":
        return "error"
    if status == "incomplete":
        details = response.get("incomplete_details")
        reason = details.get("reason") if isinstance(details, dict) else None
        return reason if isinstance(reason, str) else "incomplete"
    return status if isinstance(status, str) else None
