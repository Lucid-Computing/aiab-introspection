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

    tmp_path.mkdir(parents=True, exist_ok=True)

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


def _settings(tmp_path, prod_key=None, dev_key="k"):
    """Settings whose log sources (Prod Agent Logs, Dev Agent Logs) live under tmp_path."""
    from dataclasses import replace

    from server.config import load_settings, log_sources_from_env

    env = {"AIAB_LOG_KEY": prod_key or "", "AIAB_PROXY_KEY": dev_key or ""}
    return replace(load_settings(), logs_dir=tmp_path, log_sources=log_sources_from_env(env))


def _dev(settings):
    return settings.log_source("dev")


def test_index_dedups_orders_and_keeps_no_conversation(tmp_path):
    settings = _settings(tmp_path)
    expected = _write_logs(_dev(settings).path(settings))
    index = P.load_index(settings, _dev(settings))
    assert [r.id for r in index] == [e["id"] for e in expected]
    assert index[1].who == "tester-live" and index[1].file == "live.jsonl"
    assert index[0].file == "requests-1-2.jsonl.zst"
    assert all(r.messages == [] and r.raw == {} for r in index)
    assert index[1].message_count == 3


def test_session_title_comes_from_the_fullest_call_not_the_side_request(tmp_path):
    settings = _settings(tmp_path)
    _write_logs(_dev(settings).path(settings))
    (session,) = P.sessions(P.load_index(settings, _dev(settings)))
    assert session.title == FIRST
    assert [r.session_id for r in session.requests] == ["s1", "s1", "s1"]


def test_load_full_reads_the_call_back_from_its_file(tmp_path):
    settings = _settings(tmp_path)
    source = _dev(settings)
    expected = _write_logs(source.path(settings))
    for light, want in zip(P.load_index(settings, source), expected, strict=True):
        full = P.load_full(settings, source, light)
        assert full.raw["_file"] == light.file
        assert full.raw["id"] == want["id"] and full.who == want["who"]
        assert full.message_count == len(full.messages) == light.message_count


def test_box_records_from_the_index_match_the_full_parse(tmp_path):
    settings = _settings(tmp_path)
    expected = _write_logs(_dev(settings).path(settings))
    light = P.load_index(settings, _dev(settings))
    full = [P.parse_request(e) for e in expected]
    assert P.to_box_records(light) == P.to_box_records(full)


# ---- several sources: each its own folder, index and state ---------------------------------


def test_each_source_has_its_own_index_and_sync_state(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "_states", {})  # the module keeps one state per source id
    settings = _settings(tmp_path, prod_key="p")
    prod, dev = settings.log_source("prod"), settings.log_source("dev")
    assert prod.path(settings) == tmp_path / "prod" and dev.path(settings) == tmp_path / "dev"
    _write_logs(dev.path(settings))
    prod.path(settings).mkdir()
    (prod.path(settings) / "live.jsonl").write_text(json.dumps(entry([], "{}", who="prod-user")))
    assert [r.who for r in P.load_index(settings, prod)] == ["prod-user"]
    assert len(P.load_index(settings, dev)) == 3
    P._state(dev)["error"] = "down"
    assert P.status(dev)["error"] == "down" and P.status(prod)["error"] is None


def test_settings_default_to_dev_and_both_sources_can_be_configured(tmp_path):
    settings = _settings(tmp_path, prod_key="p", dev_key="tester:t,other:o")
    assert [s.id for s in settings.log_sources] == ["prod", "dev"]
    assert settings.log_source(None).id == "dev" and settings.log_source("nope").id == "dev"
    assert settings.log_source("prod").key == "p" and settings.log_source("dev").key == "t"
    assert settings.log_source("prod").header == "x-log-key"
    assert all(s.configured for s in settings.log_sources)
    assert not _settings(tmp_path, dev_key=None).log_source("dev").configured


def test_legacy_root_segments_move_into_dev_once_and_prod_stays(tmp_path):
    settings = _settings(tmp_path, prod_key="p")
    _write_logs(tmp_path)  # where the test proxy's files used to go
    (tmp_path / "listing.json").write_text("{}")
    (tmp_path / "prod").mkdir()
    (tmp_path / "prod" / "live.jsonl").write_text("")
    moved = P.adopt_legacy_layout(settings)
    assert sorted(moved) == ["listing.json", "live.jsonl", "requests-1-2.jsonl.zst"]
    assert not list(tmp_path.glob("*.jsonl*")) and (tmp_path / "prod" / "live.jsonl").exists()
    assert len(P.load_index(settings, _dev(settings))) == 3
    (tmp_path / "live.jsonl").write_text("")  # a stray file later: dev/ is not overwritten
    assert P.adopt_legacy_layout(settings) == []
    assert (tmp_path / "live.jsonl").exists()


# ---- the pages ---------------------------------------------------------------------------


def _client(monkeypatch, settings):
    from fastapi.testclient import TestClient

    from server import app as web
    from server.auth import Account

    monkeypatch.setattr(web, "settings", settings)
    client = TestClient(web.app)
    client.cookies.set(web.COOKIE, web.signer.issue(Account("lab", "lab", "Lab", "unused")))
    return client


def test_logs_page_pages_sessions_and_loads_earlier_messages_on_demand(tmp_path, monkeypatch):
    from server import app as web

    settings = _settings(tmp_path)
    long_talk = [{"role": "user", "content": f"turn {i}"} for i in range(10)]
    lines = []
    for i in range(web.LOGS_PAGE_SIZE + 5):  # one session each, newest last
        talk = long_talk if i == 0 else [{"role": "user", "content": f"s{i}"}]
        e = entry(talk, "{}", when=float(i))
        e["request_headers"] = {"x-claude-code-session-id": f"s{i}"}
        lines.append(json.dumps(e) + "\n")
    (tmp_path / "dev").mkdir()
    (tmp_path / "dev" / "live.jsonl").write_text("".join(lines))
    client = _client(monkeypatch, settings)

    first = client.get("/logs").text  # no ?logs: Dev Agent Logs
    assert "page 1 of 2" in first and "older →" in first
    assert first.count('class="logs-item') == web.LOGS_PAGE_SIZE
    second = client.get("/logs?page=2").text
    assert second.count('class="logs-item') == 5 and "← newer" in second

    oldest = client.get("/logs?page=2&session=s0").text
    assert 'data-src="/logs/earlier/0.0?logs=dev"' in oldest
    assert 'href="/logs/raw/0.0?logs=dev"' in oldest
    shown = '<pre class="msg-text msg-text--user">turn {}</pre>'.format
    assert shown(0) not in oldest and shown(9) in oldest  # the folded part is not sent
    earlier = client.get("/logs/earlier/0.0?logs=dev").text
    assert shown(0) in earlier and shown(3) in earlier and shown(4) not in earlier
    assert client.get("/logs/earlier/0.0?logs=prod").status_code == 404  # not in that source


def test_logs_page_selector_switches_source_and_keeps_it_in_every_link(tmp_path, monkeypatch):
    settings = _settings(tmp_path, prod_key="p")
    _write_logs(tmp_path / "dev")
    (tmp_path / "prod").mkdir()
    (tmp_path / "prod" / "live.jsonl").write_text(
        json.dumps(entry([{"role": "user", "content": "prod only"}], "{}", who="prod-user"))
    )
    client = _client(monkeypatch, settings)

    dev = client.get("/logs").text
    assert 'class="srcsel-item on" href="/logs?logs=dev"' in dev
    assert 'class="srcsel-item " href="/logs?logs=prod"' in dev
    assert "prod only" not in dev and FIRST in dev
    assert 'name="logs" value="dev"' in dev and "Dev Agent Logs · 3 requests on disk" in dev

    prod = client.get("/logs?logs=prod").text
    assert 'class="srcsel-item on" href="/logs?logs=prod"' in prod
    assert "prod only" in prod and FIRST not in prod
    assert "Prod Agent Logs · 1 requests on disk" in prod
    assert "&amp;logs=prod&amp;who=" in prod  # session links stay on the source

    unconfigured = _client(monkeypatch, _settings(tmp_path, prod_key=None)).get("/logs?logs=prod")
    assert "srcsel-item--off" in unconfigured.text and "AIAB_LOG_KEY" in unconfigured.text
    assert 'href="/logs?logs=prod"' not in unconfigured.text
