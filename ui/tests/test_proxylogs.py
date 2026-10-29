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


# ---- the light index: what the Logs and Runs pages read ----------------------------------


def _write_logs(tmp_path):
    """A sealed segment and a live part that repeats one call (the live copy wins)."""
    import zstandard

    side = entry([{"role": "user", "content": "quota"}], "{}", when=1_700_000_000.0)
    main = entry(
        [
            {"role": "user", "content": FIRST},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "now test it"},
        ],
        sse({"type": "content_block_start", "index": 0, "content_block": {"type": "text"}}),
        when=1_700_000_060.0,
    )
    later = entry([{"role": "user", "content": "again"}], "{}", when=1_700_000_120.0)
    segment = "".join(json.dumps(e) + "\n" for e in (side, main)).encode()
    (tmp_path / "requests-1-2.jsonl.zst").write_bytes(zstandard.compress(segment))
    main_live = dict(main, who="tester-live")
    (tmp_path / "live.jsonl").write_text("".join(json.dumps(e) + "\n" for e in (later, main_live)))
    return [side, main_live, later]


def _settings(tmp_path):
    from types import SimpleNamespace

    return SimpleNamespace(logs_dir=tmp_path)


def test_index_dedups_orders_and_keeps_no_conversation(tmp_path):
    expected = _write_logs(tmp_path)
    index = P.load_index(_settings(tmp_path))
    assert [r.id for r in index] == [e["id"] for e in expected]
    assert index[1].who == "tester-live" and index[1].source == "live.jsonl"
    assert index[0].source == "requests-1-2.jsonl.zst"
    assert all(r.messages == [] and r.raw == {} for r in index)
    assert index[1].message_count == 3


def test_session_title_comes_from_the_fullest_call_not_the_side_request(tmp_path):
    _write_logs(tmp_path)
    (session,) = P.sessions(P.load_index(_settings(tmp_path)))
    assert session.title == FIRST
    assert [r.session_id for r in session.requests] == ["s1", "s1", "s1"]


def test_load_full_reads_the_call_back_from_its_file(tmp_path):
    expected = _write_logs(tmp_path)
    settings = _settings(tmp_path)
    for light, want in zip(P.load_index(settings), expected, strict=True):
        full = P.load_full(settings, light)
        assert full.raw["_source"] == light.source
        assert full.raw["id"] == want["id"] and full.who == want["who"]
        assert full.message_count == len(full.messages) == light.message_count


def test_box_records_from_the_index_match_the_full_parse(tmp_path):
    expected = _write_logs(tmp_path)
    light = P.load_index(_settings(tmp_path))
    full = [P.parse_request(e) for e in expected]
    assert P.to_box_records(light) == P.to_box_records(full)
