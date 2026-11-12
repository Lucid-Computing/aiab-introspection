"""What one proxied call sends to the box: the newest turn, tools included."""

import json

import pytest

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


def test_long_tool_results_are_kept_whole_and_empty_calls_give_nothing():
    big = "x" * 300_000
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
    assert records[0]["content"] == big
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


def test_session_title_names_who_and_when_never_what_was_said(tmp_path):
    settings = _settings(tmp_path)
    _write_logs(_dev(settings).path(settings))
    (session,) = P.sessions(P.load_index(settings, _dev(settings)))
    assert session.title == f"{session.who} · {session.started_short} · 3 calls"
    assert FIRST not in session.title  # the text is for the box, not for this list (MAC-377)
    assert [r.session_id for r in session.records] == ["s1", "s1", "s1"]


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
    assert [s.id for s in settings.log_sources] == ["prod", "dev", "slack", "github", "plant"]
    assert settings.log_source(None).id == "dev" and settings.log_source("nope").id == "dev"
    assert settings.log_source("prod").key == "p" and settings.log_source("dev").key == "t"
    assert settings.log_source("prod").header == "x-log-key"
    assert all(s.configured for s in settings.log_sources if s.kind == "proxy")
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


# ---- the Slack export ------------------------------------------------------------------


def _slack_export(folder):
    """One channel: a thread of two, a message the same day outside it, one the next day."""
    folder.mkdir(parents=True, exist_ok=True)

    def row(ts, author, text, thread_ts=None, meta=()):
        return json.dumps(
            {
                "channel": "eng",
                "channel_id": "C1",
                "ts": ts,
                "author": author,
                "time": "x",
                "text": text,
                "meta": list(meta),
                "thread_ts": thread_ts,
            }
        )

    rows = [
        row("1700000000.1", "Ada <ada@x.ai> (U1)", "deploy is red", meta=["Thread: 1 replies"]),
        row("1700000100.2", "Bob <bob@x.ai> (U2)", "looking", thread_ts="1700000000.1"),
        row("1700000200.3", "Ada <ada@x.ai> (U1)", "lunch?"),
        row("1700090000.4", "Cy <cy@x.ai> (U3)", "next day"),
    ]
    (folder / "eng.jsonl").write_text("\n".join(rows) + "\n")


def _slack_settings(tmp_path):
    from dataclasses import replace

    from server.config import load_settings, log_sources_from_env

    env = {"AIAB_PROXY_KEY": "k", "AIAB_SLACK_EXPORT": str(tmp_path / "slack-export")}
    return replace(load_settings(), logs_dir=tmp_path, log_sources=log_sources_from_env(env))


def test_slack_export_is_a_source_of_messages_grouped_by_thread_and_day(tmp_path):
    from server.slacklogs import SlackMessage

    settings = _slack_settings(tmp_path)
    slack = settings.log_source("slack")
    assert slack.configured and not slack.fetches and slack.path(settings).name == "slack-export"
    _slack_export(slack.path(settings))
    index = P.load_index(settings, slack)
    assert all(isinstance(m, SlackMessage) for m in index)
    assert [m.who for m in index] == ["Ada", "Bob", "Ada", "Cy"]
    first = index[0]
    assert (first.email, first.uid, first.channel, first.ts) == (
        "ada@x.ai",
        "U1",
        "eng",
        "1700000000.1",
    )
    assert first.replies == 1 and first.notes == [] and first.thread_ts is None
    assert index[1].thread_ts == "1700000000.1" and index[1].replies is None
    assert not hasattr(first, "status") and not hasattr(first, "model")  # nothing proxy-shaped
    sessions = {s.id: s for s in P.sessions(index)}
    assert set(sessions) == {"#eng · 1700000000.1", "#eng · 2023-11-14", "#eng · 2023-11-15"}
    thread = sessions["#eng · 1700000000.1"]
    assert thread.title.startswith("#eng · ") and thread.title.endswith(" · 2 messages")
    assert "deploy" not in thread.title and [m.who for m in thread.records] == ["Ada", "Bob"]
    assert thread.model == "" and thread.started_short == "Nov 14, 10:13 PM"
    assert P.load_full(settings, slack, thread.records[-1]) is thread.records[-1]  # whole already
    assert P.raw_entry(settings, slack, thread.records[-1]) is None
    records = P.to_box_records(thread.records)
    assert [(r["agent_id"], r["kind"], r["content"]) for r in records] == [
        ("Ada", "request", "deploy is red"),
        ("Bob", "request", "looking"),
    ]
    assert records[1]["ts"] == "2023-11-14T22:15:00Z" and records[1]["session_id"] == thread.id
    assert not _settings(tmp_path).log_source("slack").configured  # no folder named


# ---- the pages ---------------------------------------------------------------------------


def _client(monkeypatch, settings):
    from fastapi.testclient import TestClient

    from server import app as web
    from server.auth import Account

    monkeypatch.setattr(web, "settings", settings)
    client = TestClient(web.app)
    client.cookies.set(web.COOKIE, web.signer.issue(Account("lab", "lab", "Lab", "unused")))
    return client


def test_logs_page_pages_sessions_and_shows_metadata_never_the_text(tmp_path, monkeypatch):
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

    assert 'class="logs-detail"' not in first  # a list only: no session is picked for you
    assert client.get("/logs?session=nope").text.count('class="logs-item') == web.LOGS_PAGE_SIZE

    oldest = client.get("/logs?page=2&session=s0").text
    # One session's page: no list beside it, and a way back to the page it came from.
    assert 'class="logs-item' not in oldest and 'class="logs-detail"' in oldest
    assert '<a href="/logs?logs=dev&amp;who=&amp;q=&amp;page=2" class="link">← Logs</a>' in oldest
    # Metadata only: who, when, how many messages. The text is for the box (MAC-377).
    assert "Messages in the request</dt><dd>10</dd>" in oldest
    assert "turn " not in oldest and "/logs/earlier/" not in oldest and "/logs/raw/" not in oldest
    assert client.get("/logs/earlier/0.0?logs=dev").status_code == 404
    assert client.get("/logs/raw/0.0?logs=dev").status_code == 404


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
    assert "prod only" not in dev and "tester · " in dev  # titles name who, not what
    assert 'name="logs" value="dev"' in dev and "Dev Agent Logs · 3 requests on disk" in dev

    prod = client.get("/logs?logs=prod").text
    assert 'class="srcsel-item on" href="/logs?logs=prod"' in prod
    assert "prod-user · " in prod and "prod only" not in prod and FIRST not in prod
    assert "Prod Agent Logs · 1 requests on disk" in prod
    assert "&amp;logs=prod&amp;who=" in prod  # session links stay on the source

    unconfigured = _client(monkeypatch, _settings(tmp_path, prod_key=None)).get("/logs?logs=prod")
    assert "srcsel-item--off" in unconfigured.text and "AIAB_LOG_KEY" in unconfigured.text
    assert 'href="/logs?logs=prod"' not in unconfigured.text
    assert "AIAB_SLACK_EXPORT" in unconfigured.text  # Slack Logs offered, disabled

    settings = _slack_settings(tmp_path)
    _slack_export(settings.log_source("slack").path(settings))
    client = _client(monkeypatch, settings)
    listing = client.get("/logs?logs=slack").text
    assert "Slack Logs · 4 messages on disk" in listing and "a static copy" in listing
    # Session ids start with "#": a raw one would become the URL fragment and drop &logs=.
    assert 'href="/logs?session=%23eng%20%C2%B7%201700000000.1&amp;logs=slack&amp;' in listing
    assert "/logs?session=#" not in listing
    assert 'name="logs" value="slack"><button class="btn btn--ghost" disabled' in listing
    assert "<td>2 msg</td>" in listing  # the session table counts messages
    slack = client.get("/logs?logs=slack&session=%23eng+%C2%B7+1700000000.1")
    assert "/logs?session=#" not in slack.text
    detail = slack.text.split('class="logs-detail"')[1]
    # Metadata only: who, when and how long, never what was said.
    assert "deploy is red" not in detail and "looking" not in detail and "lunch?" not in detail
    assert "2 messages" in detail and "<td>Ada</td>" in detail and "<td>Bob</td>" in detail
    assert "13 characters" in detail and "reply" in detail
    for proxy_only in ("· ? ·", "· 200", "raw JSON", "System prompt", "this reply", "KB"):
        assert proxy_only not in detail, proxy_only
    assert client.get("/logs/raw/eng:1700000000.1?logs=slack").status_code == 404
    assert client.get("/logs/earlier/eng:1700000000.1?logs=slack").status_code == 404


def test_runs_page_lists_slack_sessions_as_messages(tmp_path, monkeypatch):
    settings = _slack_settings(tmp_path)
    _slack_export(settings.log_source("slack").path(settings))
    page = _client(monkeypatch, settings).get("/runs?logs=slack").text
    assert "deploy is red" not in page  # the Runs page counts sessions, it does not list them
    assert "from Slack Logs" not in page  # no counts until an agreement can run
    assert 'name="logs" value="slack"' in page


# ---- Plant Logs -------------------------------------------------------------------------


def _plant_export(folder):
    """Made-up plant export: session s1 split over two runs, session s2 in a third. Every
    file starts at batch 0, as the plant writes them."""

    def records(run, session, stamps, kinds):
        (folder / run / "records").mkdir(parents=True)
        rows = [
            {
                "stream": "proxy",
                "batch": 0,
                "offset": i,
                "agent_id": "agt-1",
                "session_id": session,
                "ts": ts,
                "kind": kind,
                "content": f"secret {run} {i}",
            }
            for i, (ts, kind) in enumerate(zip(stamps, kinds, strict=True))
        ]
        (folder / run / "records" / f"{session}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows)
        )

    records(
        "s/1-first",
        "s1",
        ["2026-01-01T10:00:00Z", "2026-01-01T10:10:00Z"],
        ["request", "tool_call"],
    )
    records(
        "s/2-resumed",
        "s1",
        ["2026-01-05T09:00:00Z", "2026-01-05T09:30:00Z"],
        ["tool_result", "response"],
    )
    records("other", "s2", ["2026-01-03T00:00:00Z"], ["request"])
    (folder / "s/2-resumed/jobs.csv").write_text(
        "job,state,node,gpus_given,submitted_utc,started_utc,ended_utc,processes,gpu_devices_opened,outlived\n"
        "7,COMPLETED,gpu-01,1,2026-01-05T09:05:00Z,2026-01-05T09:05:00Z,2026-01-05T09:20:00Z,x,3,0\n"
    )
    (folder / "s/2-resumed/network.csv").write_text(
        "start_utc,from,to,port,proto,service,seconds,bytes_sent,bytes_received,state\n"
        "2026-01-05T09:10:00.5Z,a,b,443,tcp,ssl,1.0,1000,2000,SF\n"
        "2026-01-06T00:00:00Z,a,b,443,tcp,ssl,1.0,9,9,SF\n"  # after the run: not drawn
    )


def _plant_settings(tmp_path):
    from dataclasses import replace

    from server.config import load_settings, log_sources_from_env

    env = {"AIAB_PROXY_KEY": "k", "AIAB_PLANT_EXPORT": str(tmp_path / "plant-export")}
    return replace(load_settings(), logs_dir=tmp_path, log_sources=log_sources_from_env(env))


def test_plant_export_is_sent_as_written_with_a_batch_per_file(tmp_path):
    from server import plantlogs

    settings = _plant_settings(tmp_path)
    plant = settings.log_source("plant")
    assert plant.configured and not plant.fetches and not plant.chat
    assert not _settings(tmp_path).log_source("plant").configured  # no folder named
    _plant_export(plant.path(settings))
    every = P.sessions(P.load_index(settings, plant))
    # A run's jobs and network traffic are sessions of their own, so a plan can read them.
    assert {s.id for s in every if s.who.startswith("plant-")} == {
        "2-resumed-jobs",
        "2-resumed-network",
    }
    sessions = [s for s in every if not s.who.startswith("plant-")]
    assert [s.id for s in sessions] == ["s1", "s2"]  # the split session is one
    s1 = sessions[0]
    assert s1.title.startswith("agt-1 · ") and s1.title.endswith("· 4 records")
    assert "secret" not in s1.title

    sent = P.box_records([r for s in sessions for r in s.records])
    positions = {(r["stream"], r["batch"], r["offset"]) for r in sent}
    assert len(positions) == len(sent) == 5  # each file its own batch, oldest first
    assert [(r["batch"], r["offset"]) for r in sent if r["session_id"] == "s1"] == [
        (0, 0),
        (0, 1),
        (2, 0),
        (2, 1),
    ]
    keys = ("stream", "batch", "offset", "agent_id", "session_id", "ts", "kind", "content")
    assert all(tuple(r) == keys for r in sent)
    assert sent[0]["content"] == "secret s/1-first 0" and sent[0]["kind"] == "request"
    assert [r["run"] for r in plantlogs.runs(plant.path(settings))] == [
        "s/1-first",
        "other",
        "s/2-resumed",
    ]

    first, resumed = plantlogs.timeline(plant.path(settings), s1.records)
    assert (first["run"], first["batch"], resumed["batch"]) == ("s/1-first", 0, 2)
    lanes = {lane["name"]: lane for lane in resumed["lanes"]}
    assert [lane["name"] for lane in resumed["lanes"]] == list(plantlogs.LANES)
    assert lanes["Slurm jobs"]["count"] == "1" and lanes["GPU use"]["count"] == "1"
    assert lanes["Model calls"]["count"] == "1" and lanes["Network"]["count"] == "0.0 MB"
    assert len(lanes["Network"]["marks"]) == 1  # the row after the run is left out
    job = lanes["Slurm jobs"]["marks"][0]
    assert job == {"left": 16.667, "width": 50.0}
    assert {lane["name"]: lane["count"] for lane in first["lanes"]}["Tool calls"] == "1"


def test_plant_session_view_and_runs_show_metadata_only(tmp_path, monkeypatch):
    from server import runs

    settings = _plant_settings(tmp_path)
    _plant_export(settings.log_source("plant").path(settings))
    client = _client(monkeypatch, settings)
    listing = client.get("/logs?logs=plant").text
    assert (
        "Plant Logs · " in listing and "records on disk" in listing and "a static copy" in listing
    )
    assert "secret" not in listing
    page = client.get("/logs?logs=plant&session=s1").text
    detail = page.split('class="logs-detail"')[1]
    assert "s/1-first · 2026-01-01 10:00:00 to 2026-01-01 10:10:00 UTC" in detail
    assert detail.count('class="plant-run"') == 2 and "Slurm jobs" in detail
    assert "secret" not in page
    page = client.get("/runs?logs=plant").text
    assert 'name="logs" value="plant"' in page and "secret" not in page

    # An agreement pinning the plant's agent and window covers both halves.
    plan = tmp_path / "plan"
    plan.mkdir()
    scope = {
        "agents": ["agt-1"],
        "window_start": "2026-01-01T00:00:00+00:00",
        "window_end": "2026-01-06T00:00:00+00:00",
    }
    monkeypatch.setattr(runs, "plan_scope", lambda folder: scope)
    s1 = P.sessions(P.load_index(settings, settings.log_source("plant")))[0]
    times = [r.time for r in s1.records]
    assert runs.scope_notes(plan, s1.who, min(times), max(times)) == []


# ---- the Slack export bundle ------------------------------------------------------------


def _bundle_settings(tmp_path, key, folder=None):
    from dataclasses import replace

    from server.config import load_settings, log_sources_from_env

    env = {"AIAB_PROXY_KEY": "k", "AIAB_SLACK_EXPORT_KEY": key}
    if folder is not None:
        env["AIAB_SLACK_EXPORT"] = str(folder)
    return replace(
        load_settings(),
        logs_dir=tmp_path / "logs",
        log_sources=log_sources_from_env(env),
        slack_bundle=tmp_path / "bundle" / "slack-export.enc",
    )


def test_slack_bundle_round_trips_only_the_channel_files(tmp_path):
    from server import slackexport as S

    scrape = tmp_path / "scrape"
    _slack_export(scrape)
    (scrape / "eng.md").write_text("rendered, not data")
    (scrape / "sub").mkdir()
    (scrape / "sub" / "nested.jsonl").write_text("{}\n")
    key = S.new_key()
    out = tmp_path / "out" / "slack-export.enc"
    assert S.pack(scrape, key, out) == ["eng.jsonl"]
    data = out.read_bytes()
    assert data.startswith(S.MAGIC) and b"deploy is red" not in data

    dest = tmp_path / "opened"
    dest.mkdir()
    (dest / "stale.jsonl").write_text("from an earlier bundle\n")
    assert S.unpack(out, key, dest) == ["eng.jsonl"]
    assert sorted(p.name for p in dest.iterdir()) == ["eng.jsonl"]  # replaced whole
    assert (dest / "eng.jsonl").read_bytes() == (scrape / "eng.jsonl").read_bytes()
    assert not list(dest.parent.glob(".opened-*")) and not dest.with_name("opened.old").exists()

    with pytest.raises(S.BundleError, match="does not open"):
        S.unpack(out, S.new_key(), dest)
    assert (dest / "eng.jsonl").exists()  # a wrong key leaves the opened copy alone
    with pytest.raises(S.BundleError, match="base64"):
        S.unpack(out, "not a key!", dest)
    with pytest.raises(S.BundleError, match="32 bytes"):
        S.unpack(out, "c2hvcnQ=", dest)
    with pytest.raises(S.BundleError, match="not a Slack export bundle"):
        S.unpack(scrape / "eng.jsonl", key, dest)


def test_slack_source_opens_the_bundle_at_start_when_it_holds_the_key(tmp_path, monkeypatch):
    from server import slackexport as S
    from server.config import log_sources_from_env

    scrape = tmp_path / "scrape"
    _slack_export(scrape)
    key = S.new_key()
    settings = _bundle_settings(tmp_path, key)
    S.pack(scrape, key, settings.slack_bundle)

    slack = settings.log_source("slack")
    assert slack.configured and not slack.fetches and slack.key_env == "AIAB_SLACK_EXPORT_KEY"
    assert slack.path(settings) == tmp_path / "logs" / "slack"
    assert S.unpack_at_start(settings) == ["eng.jsonl"]
    monkeypatch.setattr(P, "_states", {})
    assert [r.who for r in P.load_index(settings, slack)] == ["Ada", "Bob", "Ada", "Cy"]
    page = _client(monkeypatch, settings).get("/logs?logs=slack").text
    assert "Slack Logs · 4 messages on disk" in page and str(slack.path(settings)) in page

    # A named folder wins over the key: read in place, nothing opened.
    named = _bundle_settings(tmp_path, key, folder=scrape)
    assert named.log_source("slack").path(named) == scrape
    assert named.log_source("slack").key_env == "AIAB_SLACK_EXPORT"
    assert S.unpack_at_start(named) is None

    # No key and no folder: nothing to do, the source is offered disabled.
    plain = _bundle_settings(tmp_path, "")
    assert not plain.log_source("slack").configured and S.unpack_at_start(plain) is None
    off = _client(monkeypatch, plain).get("/logs?logs=slack").text
    assert "no export configured" in off and "AIAB_SLACK_EXPORT_KEY" in off

    # The wrong key, or no bundle: a warning, the server still starts, Slack Logs is empty.
    wrong = _bundle_settings(tmp_path, S.new_key())
    assert S.unpack_at_start(wrong) == []
    missing = _bundle_settings(tmp_path / "elsewhere", key)
    assert S.unpack_at_start(missing) == []
    assert log_sources_from_env({"AIAB_SLACK_EXPORT_KEY": key})[2].configured


# ---- the GitHub export -----------------------------------------------------------------


def _github_export(folder):
    """One repository: a pull request with a comment and a review, two commits a day apart."""
    folder.mkdir(parents=True, exist_ok=True)
    base = {"repo": "aiab", "meta": []}
    rows = [
        {
            **base,
            "kind": "pull",
            "id": 1,
            "number": 7,
            "title": "Add GitHub Logs",
            "time": "2026-11-01T10:00:00Z",
            "author": "ada",
            "url": "https://github.com/x/aiab/pull/7",
            "text": "A new source.",
            "meta": ["state: merged"],
        },
        {
            **base,
            "kind": "comment",
            "id": 2,
            "number": 7,
            "title": "Add GitHub Logs",
            "time": "2026-11-01T11:00:00Z",
            "author": "bob",
            "url": "u",
            "text": "looks fine",
        },
        {
            **base,
            "kind": "review",
            "id": 3,
            "number": 7,
            "title": "Add GitHub Logs",
            "time": "2026-11-01T12:00:00Z",
            "author": "bob",
            "url": "u",
            "text": "",
            "meta": ["review: approved"],
        },
        {
            **base,
            "kind": "commit",
            "id": "abc1234def",
            "sha": "abc1234def",
            "time": "2026-11-01T09:00:00Z",
            "author": "ada",
            "url": "u",
            "text": "Fix the index\n\nlonger body",
            "meta": ["1 file changed, +2 −1"],
        },
        {
            **base,
            "kind": "commit",
            "id": "fff0000aaa",
            "sha": "fff0000aaa",
            "time": "2026-11-02T09:00:00Z",
            "author": "cy",
            "url": "u",
            "text": "Next day",
        },
    ]
    (folder / "aiab.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def _github_settings(tmp_path):
    from dataclasses import replace

    from server.config import load_settings, log_sources_from_env

    env = {"AIAB_PROXY_KEY": "k", "AIAB_GITHUB_EXPORT": str(tmp_path / "github-export")}
    return replace(load_settings(), logs_dir=tmp_path, log_sources=log_sources_from_env(env))


def test_github_export_is_a_source_of_posts_grouped_by_thread_and_commit_day(tmp_path):
    from server.githublogs import GitHubPost

    settings = _github_settings(tmp_path)
    gh = settings.log_source("github")
    assert (
        gh.configured and gh.chat and not gh.fetches and gh.path(settings).name == "github-export"
    )
    _github_export(gh.path(settings))
    index = P.load_index(settings, gh)
    assert all(isinstance(m, GitHubPost) for m in index)
    assert [m.kind for m in index] == ["commit", "pull", "comment", "review", "commit"]
    sessions = {s.id: s for s in P.sessions(index)}
    assert set(sessions) == {"aiab#7", "aiab · commits · 2026-11-01", "aiab · commits · 2026-11-02"}
    thread = sessions["aiab#7"]
    assert thread.title == "aiab#7 · 3 posts" and [m.who for m in thread.records] == [
        "ada",
        "bob",
        "bob",
    ]
    assert thread.records[0].action == "opened pull request #7"
    commits = sessions["aiab · commits · 2026-11-01"]
    assert commits.title == "aiab · commits · 2026-11-01 · 1 post"
    assert commits.records[0].action == "committed abc1234"
    assert P.load_full(settings, gh, thread.records[0]) is thread.records[0]
    assert P.raw_entry(settings, gh, thread.records[0]) is None
    records = P.to_box_records(thread.records)
    assert [(r["agent_id"], r["content"]) for r in records] == [
        ("ada", "Add GitHub Logs\n\nA new source."),
        ("bob", "looks fine"),
    ]  # the empty review sends nothing
    assert not _settings(tmp_path).log_source("github").configured


def test_logs_and_runs_pages_show_github_threads_as_posts(tmp_path, monkeypatch):
    settings = _github_settings(tmp_path)
    _github_export(settings.log_source("github").path(settings))
    client = _client(monkeypatch, settings)
    listing = client.get("/logs?logs=github").text
    assert "GitHub Logs · 5 posts on disk" in listing and "a static copy" in listing
    assert 'href="/logs?session=aiab%237&amp;logs=github&amp;' in listing
    assert "<td>3 post</td>" in listing
    page = client.get("/logs?logs=github&session=aiab%237").text
    detail = page.split('class="logs-detail"')[1]
    # Metadata only: who, when, the kind of post and its length, never the text.
    assert "A new source." not in detail and "looks fine" not in detail
    assert "Add GitHub Logs" not in detail and "aiab#7 · 3 posts" in detail
    assert "<td>ada</td>" in detail and "characters" in detail
    for proxy_only in ("raw JSON", "System prompt", "this reply"):
        assert proxy_only not in detail, proxy_only
    assert client.get("/logs/raw/aiab:pull:1?logs=github").status_code == 404
    runs = client.get("/runs?logs=github").text
    assert 'name="logs" value="github"' in runs


def test_github_source_opens_its_bundle_at_start(tmp_path, monkeypatch):
    from dataclasses import replace

    from server import githubexport as G
    from server import slackexport as S
    from server.config import load_settings, log_sources_from_env

    scrape = tmp_path / "scrape"
    _github_export(scrape)
    key = G.new_key()
    env = {"AIAB_PROXY_KEY": "k", "AIAB_GITHUB_EXPORT_KEY": key}
    settings = replace(
        load_settings(),
        logs_dir=tmp_path / "logs",
        log_sources=log_sources_from_env(env),
        github_bundle=tmp_path / "bundle" / "github-export.enc",
    )
    G.pack(scrape, key, settings.github_bundle, G.MAGIC, G.KEY_ENV)
    data = settings.github_bundle.read_bytes()
    assert data.startswith(G.MAGIC) and b"A new source." not in data
    with pytest.raises(S.BundleError, match="not a Slack export bundle"):
        S.unpack(settings.github_bundle, key, tmp_path / "x")  # one kind is not the other

    gh = settings.log_source("github")
    assert gh.configured and gh.key_env == "AIAB_GITHUB_EXPORT_KEY"
    assert G.unpack_at_start(settings) == ["aiab.jsonl"]
    monkeypatch.setattr(P, "_states", {})
    assert len(P.load_index(settings, gh)) == 5
    assert G.unpack_at_start(replace(settings, github_bundle=tmp_path / "none.enc")) == []
    plain = replace(settings, log_sources=log_sources_from_env({"AIAB_PROXY_KEY": "k"}))
    assert G.unpack_at_start(plain) is None
    off = _client(monkeypatch, plain).get("/logs?logs=github").text
    assert "no export configured" in off and "AIAB_GITHUB_EXPORT_KEY" in off


def test_github_fetch_reads_only_public_repositories(tmp_path, monkeypatch):
    from server import githubexport as G

    repos = [
        {"name": "b", "full_name": "o/b", "private": False, "visibility": "public", "fork": False},
        {"name": "a", "full_name": "o/a", "private": False, "visibility": "public", "fork": True},
        {"name": "c", "full_name": "o/c", "private": True, "visibility": "private", "fork": False},
    ]
    asked = []

    def gh(path):
        asked.append(path)
        if path.startswith("orgs/"):
            return repos
        if path == "repos/o/b":
            return {"private": False, "visibility": "public"}
        if path.startswith("repos/o/b/issues?"):
            return [
                {
                    "number": 1,
                    "id": 10,
                    "title": "T",
                    "state": "open",
                    "created_at": "2026-01-01T00:00:00Z",
                    "user": {"login": "ada"},
                    "html_url": "h",
                    "body": "b",
                    "labels": [],
                }
            ]
        if path.startswith("repos/o/b/commits?"):
            return [
                {
                    "sha": "s1",
                    "html_url": "h",
                    "author": None,
                    "commit": {
                        "message": "m",
                        "author": {"name": "Ada", "date": "2026-01-02T00:00:00Z"},
                    },
                }
            ]
        if path == "repos/o/b/commits/s1":
            return {"files": [{"filename": "x.py"}], "stats": {"additions": 1, "deletions": 0}}
        return []

    monkeypatch.setattr(G, "_gh", gh)
    (tmp_path / "gone.jsonl").write_text("{}\n")
    assert G.fetch(tmp_path, "o", log=lambda _: None) == ["b.jsonl"]
    assert not any("o/a" in p or "o/c" in p for p in asked)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["b.jsonl"]
    rows = [json.loads(line) for line in (tmp_path / "b.jsonl").read_text().splitlines()]
    assert [(r["kind"], r["author"]) for r in rows] == [("issue", "ada"), ("commit", "Ada")]
    assert rows[1]["meta"] == ["1 file changed, +1 −0", "files: x.py"]

    monkeypatch.setattr(G, "_gh", lambda path: {"private": True} if path == "repos/o/b" else [])
    with pytest.raises(G.FetchError, match="not public"):
        G.repo_rows(repos[0])
