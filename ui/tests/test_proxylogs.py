"""What one proxied call sends to the box: the newest turn, tools included."""

import json

from server import proxylogs as P


def entry(messages, reply, when=1_700_000_000.0, who="tester"):
    return {
        "id": str(when),
        "time": when,
        "who": who,
        "path": "/v1/messages",
        "request_headers": {"x-claude-code-session-id": "s1"},
        "request_raw": {"text": json.dumps({"model": "m", "messages": messages})},
        "response_raw": {"text": reply},
    }


def sse(*events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


FIRST = "Write the sorter described in TODO.md"


def test_tool_turn_sends_the_tool_result_not_the_first_prompt_again():
    messages = [
        {"role": "user", "content": FIRST},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"p": "x"}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "def sort():"}],
        },
    ]
    reply = sse(
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Running it."},
        },
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "name": "Bash"},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"command": "pytest"}'},
        },
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
    )
    records = P.to_box_records([P.parse_request(entry(messages, reply))])
    assert [r["kind"] for r in records] == ["tool_result", "response", "tool_call"]
    assert records[0]["content"] == "def sort():"
    assert records[1]["content"] == "Running it."
    assert records[2]["content"].startswith("Bash ") and "pytest" in records[2]["content"]
    assert all(FIRST not in r["content"] for r in records)
    assert [r["offset"] for r in records] == [0, 1, 2]


def test_streamed_tool_only_reply_gives_a_tool_call_record_not_no_text():
    messages = [{"role": "user", "content": FIRST}]
    reply = sse(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "name": "Edit"},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"f": "a.py"}'},
        },
    )
    records = P.to_box_records([P.parse_request(entry(messages, reply))])
    assert [r["kind"] for r in records] == ["request", "tool_call"]
    assert records[0]["content"] == FIRST
    assert "(no text)" not in json.dumps(records)


def test_long_tool_results_are_cut_and_empty_calls_give_nothing():
    big = "x" * 10_000
    messages = [
        {"role": "user", "content": FIRST},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": big}]},
    ]
    records = P.to_box_records([P.parse_request(entry(messages, "{}"))])
    assert [r["kind"] for r in records] == ["tool_result"]
    assert len(records[0]["content"]) < 2_100 and "cut here" in records[0]["content"]
    # A harness reminder answered by an API error is not a record at all.
    reminder = [{"role": "user", "content": "<system-reminder>a file changed</system-reminder>"}]
    error = json.dumps({"type": "error", "error": {"type": "overloaded_error", "message": "x"}})
    assert P.to_box_records([P.parse_request(entry(reminder, error))]) == []
