import hashlib
import os

import pytest
from conftest import ANSWER, API_KEY, LIVE, SECRET, ProxyDir, entry, lines

from logserver import calls, config
from logserver import store as store_module
from logserver.config import Settings
from logserver.store import Store, StoreError


def batch(prefix, n, t=1_800_001_000.0):
    return [entry(f"{prefix}{i}", "carol", t + i, f"question {i}", f"answer {i}") for i in range(n)]


def ids(store):
    return [c.id for c in store.calls()]


@pytest.fixture
def reads(monkeypatch):
    """Every sealed or pending file parsed, by name, in order."""
    seen = []
    original = store_module._read

    def spy(f, compressed):
        seen.append(os.path.basename(f.name))
        return original(f, compressed)

    monkeypatch.setattr(store_module, "_read", spy)
    return seen


@pytest.fixture
def parsed(monkeypatch):
    """Every line parsed as a call, by id: the live file is read on, not again."""
    seen = []
    original = calls.parse

    def spy(raw):
        seen.append(raw.get("id"))
        return original(raw)

    monkeypatch.setattr(calls, "parse", spy)
    return seen


def problems(store):
    return {p["file"]: p["problem"] for p in store.status["problems"]}


def test_the_first_read_takes_the_sealed_segment_and_the_live_file(store):
    assert store.status["result"] == {"new": 1, "segments": 1, "live": 1, "calls": 3}
    assert store.status["error"] is None and store.status["problems"] == []
    assert ids(store) == ["a1", "a2", "b1"]


def test_a_second_read_parses_nothing_again(store, reads, parsed):
    assert store.refresh() == {"new": 0, "segments": 1, "live": 1, "calls": 3}
    assert reads == [] and parsed == []


def test_sealed_pending_and_live_are_read_in_stream_order(settings, proxy, reads):
    proxy.rotate()  # b1 becomes pending (seq 3)
    proxy.append(batch("c", 2))
    store = Store(settings)
    assert store.refresh() == {"new": 2, "segments": 2, "live": 2, "calls": 5}
    assert ids(store) == ["a1", "a2", "b1", "c0", "c1"]
    assert reads == ["requests-000000001-000000002.jsonl.zst", "requests-000000003-000000003.jsonl"]


def test_a_pending_file_that_becomes_its_zst_is_not_parsed_again(settings, proxy, reads):
    pending = proxy.rotate()
    store = Store(settings)
    store.refresh()
    proxy.compress(pending)
    reads.clear()
    assert store.refresh()["new"] == 0
    assert reads == [] and ids(store) == ["a1", "a2", "b1"] and store.status["problems"] == []


def test_a_pending_file_and_its_zst_both_present_are_read_once_from_the_zst(settings, proxy, reads):
    pending = proxy.rotate()
    proxy.compress(pending, keep_pending=True)  # a crash before the proxy's recover() ran
    store = Store(settings)
    assert store.refresh()["segments"] == 2
    assert reads == [
        "requests-000000001-000000002.jsonl.zst",
        "requests-000000003-000000003.jsonl.zst",
    ]
    assert ids(store) == ["a1", "a2", "b1"]


def test_a_rotation_between_opening_the_live_file_and_listing_is_caught(
    settings, proxy, monkeypatch
):
    store = Store(settings)
    store.refresh()
    proxy.append(batch("c", 2))
    listed = Store._list
    rotated = []

    def rotate_first(self):
        if not rotated:  # the live file is open by now: rotate under it
            rotated.append(proxy.rotate())
            proxy.append(batch("d", 1, t=1_800_002_000.0))
        return listed(self)

    monkeypatch.setattr(Store, "_list", rotate_first)
    assert store.refresh() == {"new": 1, "segments": 2, "live": 1, "calls": 6}
    assert ids(store) == ["a1", "a2", "b1", "c0", "c1", "d0"]
    assert store.status["problems"] == []


def test_a_live_file_that_keeps_rotating_is_read_next_time(settings, proxy, monkeypatch):
    store = Store(settings)
    listed = Store._list

    def rotate_every_time(self):
        proxy.append(batch(f"r{proxy.seq}-", 1))
        proxy.rotate()
        return listed(self)

    monkeypatch.setattr(Store, "_list", rotate_every_time)
    result = store.refresh()
    assert result["live"] == 0 and "kept rotating" in problems(store)["requests.jsonl"]
    assert ids(store)[:3] == ["a1", "a2", "b1"]  # everything rotated so far is read
    monkeypatch.setattr(Store, "_list", listed)
    store.refresh()
    assert store.status["problems"] == []


def test_a_pending_file_compressed_after_the_listing_is_read_from_its_zst(
    settings, proxy, monkeypatch, reads
):
    pending = proxy.rotate()
    listed = Store._list

    def compress_after(self):
        found = listed(self)
        if pending.exists():
            proxy.compress(pending)
        return found

    monkeypatch.setattr(Store, "_list", compress_after)
    store = Store(settings)
    store.refresh()
    assert ids(store) == ["a1", "a2", "b1"] and store.status["problems"] == []
    assert reads[-1] == "requests-000000003-000000003.jsonl.zst"


def test_a_pending_file_deleted_after_the_listing_skips_that_file_only(
    settings, proxy, monkeypatch
):
    pending = proxy.rotate()
    proxy.append(batch("c", 1))
    listed = Store._list

    def delete_after(self):
        found = listed(self)
        pending.unlink(missing_ok=True)
        return found

    monkeypatch.setattr(Store, "_list", delete_after)
    store = Store(settings)
    store.refresh()
    assert ids(store) == ["a1", "a2", "c0"]
    assert problems(store) == {
        "segments/requests-000000003-000000003.jsonl": "gone before it could be read",
    }
    store.refresh()  # the next listing ends before the live file seen: said, not hidden
    assert "nothing changed" in problems(store)["segments"]


def test_a_corrupt_zst_is_skipped_reported_and_not_tried_again(settings, proxy, reads):
    zst = proxy.seal(batch("c", 2))  # b1, c0, c1
    zst.write_bytes(b"not zstd at all")
    proxy.append(batch("d", 1, t=1_800_002_000.0))
    store = Store(settings)
    store.refresh()
    assert ids(store) == ["a1", "a2", "d0"]
    assert "cannot be read" in problems(store)[f"segments/{zst.name}"]
    reads.clear()
    store.refresh()
    assert reads == [] and "cannot be read" in problems(store)[f"segments/{zst.name}"]


def test_a_truncated_zst_holds_no_calls_rather_than_some(settings, proxy):
    zst = proxy.seal(batch("c", 50))  # b1 and c0..c49
    zst.write_bytes(zst.read_bytes()[: zst.stat().st_size // 2])  # decompresses without error
    store = Store(settings)
    store.refresh()
    assert ids(store) == ["a1", "a2"]
    assert "its name says 51" in problems(store)[f"segments/{zst.name}"]


def test_a_torn_last_line_is_taken_once_it_is_whole(settings, proxy):
    whole = lines(batch("c", 1))[:-1]
    proxy.append(raw=whole[:40])
    store = Store(settings)
    assert store.refresh()["live"] == 1 and ids(store) == ["a1", "a2", "b1"]
    with proxy.live.open("ab") as f:
        f.write(whole[40:] + b"\n")
    assert store.refresh()["live"] == 2 and ids(store) == ["a1", "a2", "b1", "c0"]
    assert store.status["problems"] == []


def test_a_torn_line_the_proxy_ends_on_restart_is_one_unreadable_line(settings, proxy):
    proxy.append(raw=b'{"id": "cut sho')
    store = Store(settings)
    store.refresh()
    with proxy.live.open("ab") as f:  # what the proxy's _load_container does on its next start
        f.write(b"\n")
    proxy.append(batch("c", 1))
    assert store.refresh()["live"] == 3
    assert ids(store) == ["a1", "a2", "b1", "c0"]
    assert problems(store) == {"requests.jsonl": "1 lines are not calls"}


def test_a_line_that_breaks_the_parser_is_one_unreadable_line(settings, proxy):
    deep = {
        **entry("deep", "eve", 1_800_002_000.0, "x", "y"),
        "request_raw": {"text": "[" * 100_000},
    }
    proxy.append([deep, *batch("c", 1)])
    store = Store(settings)
    store.refresh()
    assert ids(store) == ["a1", "a2", "b1", "c0"]
    assert problems(store) == {"requests.jsonl": "1 lines are not calls"}


def test_an_unreadable_line_is_skipped_and_counted(settings, proxy):
    proxy.append(raw=b"not json\n")
    proxy.append(batch("c", 1))
    zst = proxy.seal([])  # the bad line is now sealed, in a segment with c0
    store = Store(settings)
    store.refresh()
    assert ids(store) == ["a1", "a2", "b1", "c0"]
    assert problems(store) == {f"segments/{zst.name}": "1 lines are not calls"}


def test_the_live_file_is_read_on_from_where_it_stopped(settings, proxy, parsed):
    store = Store(settings)
    store.refresh()
    parsed.clear()
    proxy.append(batch("c", 2))
    assert store.refresh() == {"new": 0, "segments": 1, "live": 3, "calls": 5}
    assert parsed == ["c0", "c1"]
    assert ids(store) == ["a1", "a2", "b1", "c0", "c1"]


def test_two_rotations_between_reads(settings, proxy, reads):
    store = Store(settings)
    store.refresh()
    proxy.append(batch("c", 2))
    proxy.compress(proxy.rotate())  # b1, c0, c1: seqs 3-5
    proxy.append(batch("d", 1, t=1_800_002_000.0))
    proxy.rotate()  # d0: seq 6, still pending
    proxy.append(batch("e", 1, t=1_800_003_000.0))
    reads.clear()
    # The live list starts again: b1, read live before, is held once (in its segment).
    assert store.refresh() == {"new": 2, "segments": 3, "live": 1, "calls": 7}
    assert reads == ["requests-000000003-000000005.jsonl.zst", "requests-000000006-000000006.jsonl"]
    assert ids(store) == ["a1", "a2", "b1", "c0", "c1", "d0", "e0"]
    assert len(store._live.calls) == 1


def test_no_segments_yet(tmp_path):
    proxy = ProxyDir(tmp_path / "fresh")
    proxy.append(LIVE)
    store = Store(
        Settings(proxy_dir=proxy.root, api_key=API_KEY, box_host="b", box_repo="r", sync_seconds=1)
    )
    assert store.refresh() == {"new": 0, "segments": 0, "live": 1, "calls": 1}
    assert store.status["problems"] == []


def test_a_new_empty_live_file_with_no_segments_drops_the_old_calls(tmp_path):
    proxy = ProxyDir(tmp_path / "fresh")
    proxy.append(LIVE)
    store = Store(
        Settings(proxy_dir=proxy.root, api_key=API_KEY, box_host="b", box_repo="r", sync_seconds=1)
    )
    store.refresh()
    held = proxy.live.with_name("held")  # renamed, not deleted: its inode is not reused
    os.replace(proxy.live, held)
    proxy.live.touch()
    assert store.refresh() == {"new": 0, "segments": 0, "live": 0, "calls": 0}
    assert ids(store) == []


def test_a_zst_being_written_is_ignored(settings, proxy):
    (proxy.segments / "requests-000000003-000000003.jsonl.zst.tmp").write_bytes(b"half")
    store = Store(settings)
    store.refresh()
    assert ids(store) == ["a1", "a2", "b1"] and store.status["problems"] == []


def test_a_segment_missing_from_a_later_listing_keeps_its_calls(settings, proxy):
    proxy.seal(batch("c", 1))  # seq 4, so the first segment is not the last
    store = Store(settings)
    store.refresh()
    first = proxy.segments / "requests-000000001-000000002.jsonl.zst"
    first.rename(proxy.root / "elsewhere")
    store.refresh()
    assert ids(store) == ["a1", "a2", "b1", "c0"]
    assert problems(store) == {f"segments/{first.name}": "gone; its calls are kept"}


def test_a_log_that_starts_again_is_read_afresh_after_a_second_look(settings, proxy):
    store = Store(settings)
    store.refresh()
    for p in proxy.segments.iterdir():
        p.unlink()
    proxy.live.write_bytes(b"")
    proxy.append(batch("n", 1))
    store.refresh()  # first look: nothing changes
    assert ids(store) == ["a1", "a2", "b1"]
    assert "nothing changed" in problems(store)["segments"]
    store.refresh()
    assert ids(store) == ["n0"]


def test_nothing_in_the_proxy_directory_is_written(settings, proxy):
    pending = proxy.rotate()
    proxy.append(raw=b'{"torn')

    def snapshot():
        return {
            p.relative_to(proxy.root): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat())
            for p in proxy.root.rglob("*")
            if p.is_file()
        }

    def stamps(snap):
        return {k: (h, s.st_mtime_ns, s.st_size) for k, (h, s) in snap.items()}

    before = stamps(snapshot())
    dirs = [proxy.root, proxy.segments]
    dir_stamps = [d.stat().st_mtime_ns for d in dirs]
    if os.geteuid() != 0:  # root ignores the mode bits: then the hashes are the whole check
        for p in [*proxy.root.rglob("*"), proxy.root]:
            p.chmod(0o555 if p.is_dir() else 0o444)
    try:
        store = Store(settings)
        store.refresh()
        store.refresh()
    finally:
        for p in [proxy.root, *proxy.root.rglob("*")]:
            p.chmod(0o755 if p.is_dir() else 0o644)
    assert ids(store) == ["a1", "a2", "b1"] and pending.exists()
    assert stamps(snapshot()) == before
    assert [d.stat().st_mtime_ns for d in dirs] == dir_stamps


def test_a_missing_proxy_directory_is_an_error_and_keeps_the_last_index(store, settings):
    os.rename(settings.proxy_dir, settings.proxy_dir.with_name("moved"))
    with pytest.raises(StoreError, match="missing"):
        store.refresh()
    assert store.status["error"] and ids(store) == ["a1", "a2", "b1"]
    assert store.status["result"]["calls"] == 3


def test_calls_group_into_sessions_and_become_box_records(store):
    found = calls.sessions(list(store.calls()))
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


def test_settings_stop_at_a_missing_key():
    env = {"BOX_HOST": "h", "BOX_REPO": "r"}
    with pytest.raises(config.ConfigError, match="API_KEY"):
        config.load(env)
    loaded = config.load({**env, "API_KEY": "a"})
    assert str(loaded.proxy_dir) == "/proxy-data" and loaded.sync_seconds == 300
    assert config.load({**env, "API_KEY": "a", "PROXY_DIR": "/elsewhere"}).proxy_dir.name == (
        "elsewhere"
    )
