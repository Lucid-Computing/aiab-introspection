"""Codex's calls (OpenAI's Responses API), stored the way the logging proxy stores them."""

import base64
import gzip
import json

import zstandard

from logserver import calls, codex

PATH = "/chatgpt/backend-api/codex/responses"
TYPED = "why does the build fail"
RESULT = "exit code 1: missing header"
ANSWER = "The header is not installed."


def conversation(extra=()):
    return [
        {"type": "message", "role": "developer", "content": "You are Codex."},
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "<environment_context>cwd</environment_context>"},
                {"type": "input_text", "text": TYPED},
            ],
        },
        *extra,
    ]


def stream(*events) -> str:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


def answer_stream(text=ANSWER, done=True) -> str:
    events = [
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "message", "role": "assistant", "content": []},
        },
        *[
            {
                "type": "response.output_text.delta",
                "output_index": 0,
                "content_index": 0,
                "delta": piece,
            }
            for piece in (text[:5], text[5:])
        ],
    ]
    if done:
        events.append({"type": "response.completed", "response": {"status": "completed"}})
    return stream(*events)


def entry(request, reply, path=PATH, headers=None, encoding=""):
    raw = json.dumps(request).encode()
    headers = {"thread-id": "t-1", **(headers or {})}
    if encoding == "zstd":
        raw = zstandard.ZstdCompressor().compress(raw)
    elif encoding == "gzip":
        raw = gzip.compress(raw)
    if encoding:
        headers["content-encoding"] = encoding
        stored = {"base64": base64.b64encode(raw).decode()}
    else:
        stored = {"text": raw.decode()}
    return {
        "id": "c1",
        "who": "alice",
        "time": 1_800_000_000.0,
        "status": 200,
        "path": path,
        "request_headers": headers,
        "request_raw": stored,
        "response_raw": {"text": reply if isinstance(reply, str) else json.dumps(reply)},
    }


def test_a_codex_call_gives_what_was_typed_and_the_answer():
    call, parts = calls.parse(entry({"model": "gpt-6", "input": conversation()}, answer_stream()))
    assert parts == (("request", TYPED), ("response", ANSWER))
    assert call.session_id == "t-1"
    assert call.model == "gpt-6"
    assert call.stop_reason == "completed"
    assert call.kinds == ("request", "response")
    assert call.message_count == 1  # the developer message is instructions, not conversation


def test_a_compressed_body_is_unpacked_first():
    request = {"model": "gpt-6", "input": conversation()}
    for encoding in ("zstd", "gzip"):
        _, parts = calls.parse(entry(request, answer_stream(), encoding=encoding))
        assert parts == (("request", TYPED), ("response", ANSWER))


def test_a_body_that_does_not_unpack_leaves_a_call_with_no_parts():
    broken = entry({"input": conversation()}, answer_stream())
    broken["request_headers"]["content-encoding"] = "zstd"
    broken["request_raw"] = {"base64": base64.b64encode(b"not zstd at all").decode()}
    call, parts = calls.parse(broken)
    assert parts == ()
    assert call.session_id == "t-1"  # still in its session, by the header


def test_a_body_that_unpacks_too_large_is_not_read(monkeypatch):
    monkeypatch.setattr(codex, "MAX_BODY_BYTES", 64)
    _, parts = calls.parse(entry({"input": conversation()}, answer_stream(), encoding="zstd"))
    assert parts == ()


def test_only_the_newest_turn_is_sent_with_its_tool_results():
    earlier = [
        {"type": "function_call", "call_id": "x", "name": "shell", "arguments": '{"cmd": "make"}'},
        {"type": "function_call_output", "call_id": "x", "output": RESULT},
    ]
    reply = {
        "object": "response",
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": [], "encrypted_content": "…"},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": ANSWER}],
            },
            {"type": "function_call", "call_id": "y", "name": "shell", "arguments": '{"cmd":"ls"}'},
        ],
    }
    _, parts = calls.parse(entry({"input": conversation(earlier)}, reply))
    assert [kind for kind, _ in parts] == ["tool_result", "response", "tool_call"]
    assert parts[0][1] == RESULT
    assert parts[2][1].startswith("shell ") and '"cmd": "ls"' in parts[2][1]
    assert TYPED not in json.dumps(parts)  # it went with its own call


def test_a_stream_cut_off_keeps_what_arrived():
    cut = stream(
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "function_call", "name": "shell", "arguments": ""},
        },
        {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": '{"cm'},
    )
    call, parts = calls.parse(entry({"input": conversation()}, cut))
    assert parts == (("request", TYPED), ("tool_call", 'shell {"cm'))
    assert call.stop_reason is None


def test_a_failed_reply_ends_in_error_and_sends_no_answer():
    failed = stream({"type": "response.failed", "response": {"status": "failed", "error": {}}})
    call, parts = calls.parse(entry({"input": conversation()}, failed))
    assert parts == (("request", TYPED),)
    assert call.stop_reason == "error"


def test_the_session_falls_back_to_the_body():
    request = {"input": conversation(), "client_metadata": {"thread_id": "t-body"}}
    e = entry(request, answer_stream())
    del e["request_headers"]["thread-id"]
    call, _ = calls.parse(e)
    assert call.session_id == "t-body"


def test_a_codex_call_that_is_no_model_request_has_no_parts():
    call, parts = calls.parse(entry({}, {"models": []}, path="/openai/v1/models"))
    assert parts == ()
    assert call.session_id == "t-1"


def test_claude_calls_still_go_to_the_claude_reader():
    request = {"model": "claude-haiku-5-5", "messages": [{"role": "user", "content": "hi"}]}
    reply = {"content": [{"type": "text", "text": "hello"}], "stop_reason": "end_turn"}
    e = entry(request, reply, path="/v1/messages", headers={"x-claude-code-session-id": "s-1"})
    call, parts = calls.parse(e)
    assert parts == (("request", "hi"), ("response", "hello"))
    assert call.session_id == "s-1"


def test_odd_shapes_do_not_break_the_reader():
    odd = {"input": [None, 3, {"type": "message", "role": "user", "content": [None, {"type": 7}]}]}
    for reply in (
        "",
        "data: not json\n",
        "data: [1]\n",
        {"output": "x"},
        {"error": {"message": "m"}},
    ):
        calls.parse(entry(odd, reply))
