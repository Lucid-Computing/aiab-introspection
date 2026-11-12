from dataclasses import replace

import pytest
from conftest import ANSWER, SECRET

from logserver import calls, config
from logserver.store import ProxyError, Store


def test_sync_downloads_the_segment_and_the_live_part(store, settings, proxy):
    assert store.status["result"] == {"new": 1, "segments": 1, "live": 1, "calls": 3}
    assert (settings.data_dir / proxy.name).read_bytes() == proxy.segment
    assert [c.id for c in store.load()] == ["a1", "a2", "b1"]


def test_a_second_sync_downloads_no_segment_again(store):
    assert store.sync()["new"] == 0


def test_a_segment_that_does_not_match_its_listed_hash_is_refused(settings, proxy):
    proxy.sha = "0" * 64
    with pytest.raises(ProxyError, match="do not match"):
        Store(settings).sync()
    assert not (settings.data_dir / proxy.name).exists()


def test_a_wrong_proxy_key_is_reported_not_swallowed(settings):
    store = Store(replace(settings, proxy_key="wrong"))
    with pytest.raises(ProxyError, match="refused the key"):
        store.sync()
    assert store.status["error"] == "the proxy refused the key"


def test_calls_group_into_sessions_and_become_box_records(store):
    found = calls.sessions(store.load())
    assert [(s.id, s.who, len(s.calls)) for s in found] == [
        ("bob-1800000500", "bob", 1),  # no session header: who and a 30-minute gap
        ("s-alice", "alice", 2),
    ]
    records = calls.box_records(list(found[1].calls))
    assert [(r["offset"], r["kind"], r["content"]) for r in records] == [
        (0, "request", SECRET),
        (1, "response", ANSWER),
        (2, "request", "and the second question"),
        (3, "response", "second answer"),
    ]
    assert {r["agent_id"] for r in records} == {"alice"}
    assert records[0]["ts"] == "2027-01-15T08:00:00Z"


def test_a_line_that_does_not_parse_is_skipped(settings, proxy):
    proxy.live = b"not json\n" + proxy.live
    store = Store(settings)
    store.sync()
    assert len(store.load()) == 3


def test_settings_stop_at_a_missing_key():
    env = {"PROXY_URL": "http://p", "PROXY_KEY": "k", "BOX_HOST": "h", "BOX_REPO": "r"}
    with pytest.raises(config.ConfigError, match="API_KEY"):
        config.load(env)
    loaded = config.load({**env, "API_KEY": "a"})
    assert loaded.proxy_header == "x-log-key" and loaded.sync_seconds == 300
