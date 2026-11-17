import gc
import hashlib
import json
import os
import threading
import tracemalloc

import pytest
import zstandard
from conftest import (
    ANSWER,
    API_KEY,
    LIVE,
    SEALED,
    SECRET,
    ProxyDir,
    compress,
    entry,
    lines,
)

from logserver import calls, config
from logserver import store as store_module
from logserver.config import Settings, Target
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

    def spy(f, compressed, first):
        seen.append(os.path.basename(f.name))
        return original(f, compressed, first)

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


def by_hand(proxy, where) -> bytes:
    """The line a call's ``Where`` names, read without the store: the file whose name holds
    its seq (or the live file), uncompressed whole, from the offset to the next newline."""
    data = None
    for path in sorted(proxy.segments.glob("requests-*")) if proxy.segments.exists() else ():
        m = store_module.NAME_RE.match(path.name)
        if m and int(m[1]) <= where.seq <= int(m[2]):
            raw = path.read_bytes()
            data = zstandard.ZstdDecompressor().decompress(raw) if m[3] else raw
    if data is None:
        data = proxy.live.read_bytes()
    line = data[where.offset : data.index(b"\n", where.offset) + 1]
    assert hashlib.sha256(line).digest() == where.sha256
    return line


def located(store, proxy) -> dict[str, tuple[int, str]]:
    """Each call's seq, and the id of the line its ``Where`` names, read by hand."""
    return {c.id: (c.where.seq, json.loads(by_hand(proxy, c.where))["id"]) for c in store.calls()}


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
        Settings(proxy_dir=proxy.root, boxes=(Target("dev", API_KEY, "b", "r"),), sync_seconds=1)
    )
    assert store.refresh() == {"new": 0, "segments": 0, "live": 1, "calls": 1}
    assert store.status["problems"] == []


def test_a_new_empty_live_file_with_no_segments_drops_the_old_calls(tmp_path):
    proxy = ProxyDir(tmp_path / "fresh")
    proxy.append(LIVE)
    store = Store(
        Settings(proxy_dir=proxy.root, boxes=(Target("dev", API_KEY, "b", "r"),), sync_seconds=1)
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


def test_a_segment_missing_from_a_later_listing_keeps_its_calls_listed(settings, proxy):
    proxy.seal(batch("c", 1))  # seq 4, so the first segment is not the last
    store = Store(settings)
    store.refresh()
    first = proxy.segments / "requests-000000001-000000002.jsonl.zst"
    first.rename(proxy.root / "elsewhere")
    store.refresh()
    assert ids(store) == ["a1", "a2", "b1", "c0"]
    assert problems(store) == {
        f"segments/{first.name}": "gone; its calls are still listed but can no longer be exported"
    }


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
    alice = list(found[1].calls)
    records = calls.box_records(list(zip(alice, store.texts(alice), strict=True)))
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


def test_one_box_from_the_plain_names():
    loaded = config.load({"API_KEY": "s3cret", "BOX_HOST": "h", "BOX_REPO": "r"})
    assert [(b.name, b.api_key, b.box_host, b.box_repo) for b in loaded.boxes] == [
        ("default", "s3cret", "h", "r")
    ]
    assert "s3cret" not in repr(loaded)  # a key is never printed


def test_several_boxes_each_with_its_own_key():
    env = {
        "BOXES": "dev, Prod",
        "DEV_API_KEY": "d",
        "DEV_BOX_HOST": "dev.example",
        "DEV_BOX_REPO": "org/dev",
        "PROD_API_KEY": "p",
        "PROD_BOX_HOST": "prod.example",
        "PROD_BOX_REPO": "org/prod",
    }
    loaded = config.load(env)
    assert [(b.name, b.api_key, b.box_host) for b in loaded.boxes] == [
        ("dev", "d", "dev.example"),
        ("prod", "p", "prod.example"),
    ]
    with pytest.raises(config.ConfigError, match="PROD_BOX_REPO"):
        config.load({k: v for k, v in env.items() if k != "PROD_BOX_REPO"})
    with pytest.raises(config.ConfigError, match="share an API key"):
        config.load({**env, "PROD_API_KEY": "d"})
    with pytest.raises(config.ConfigError, match="twice"):
        config.load({**env, "BOXES": "dev,dev"})
    with pytest.raises(config.ConfigError, match="not a name"):
        config.load({**env, "BOXES": "dev,pr-od"})
    with pytest.raises(config.ConfigError, match="not both"):
        config.load({**env, "API_KEY": "a"})


def test_each_call_knows_where_its_line_is(store, proxy):
    assert located(store, proxy) == {"a1": (1, "a1"), "a2": (2, "a2"), "b1": (3, "b1")}
    assert [c.where.offset for c in store.calls()] == [0, len(lines(SEALED[:1])), 0]


def test_a_call_is_found_at_the_same_place_after_its_file_moves(settings, proxy):
    proxy.append(batch("c", 2))
    store = Store(settings)
    store.refresh()
    before = located(store, proxy)
    pending = proxy.rotate()  # the live file, b1 c0 c1, is now pending: same bytes, same offsets
    assert located(store, proxy) == before
    proxy.compress(pending)  # and now compressed
    assert located(store, proxy) == before
    assert before == {
        "a1": (1, "a1"),
        "a2": (2, "a2"),
        "b1": (3, "b1"),
        "c0": (4, "c0"),
        "c1": (5, "c1"),
    }


def test_every_line_counts_for_seq_and_offset(settings, proxy):
    """Blank, unreadable and restart-ended torn lines take a seq and bytes too; a call after
    them, or with multi-byte text, is still found."""
    accents = entry("u1", "zoë", 1_800_003_000.0, "ça va ? 日本語 🙂", "très bien")
    proxy.append(raw=b"\n")
    proxy.append(raw=b"not json\n")
    proxy.append(raw=b'{"id": "cut sho' + b"\n")  # a torn line the proxy ended on restart
    proxy.append([accents, *batch("c", 1)])
    store = Store(settings)
    store.refresh()
    assert located(store, proxy)["u1"] == (7, "u1")
    assert located(store, proxy)["c0"] == (8, "c0")
    proxy.seal([])
    store.refresh()
    assert located(store, proxy)["c0"] == (8, "c0")


# ---- reading chosen calls back -------------------------------------------------------------


def chosen(store, *wanted):
    """The indexed calls with these ids, in the order given."""
    by_id = {c.id: c for c in store.calls()}
    return [by_id[i] for i in wanted]


def read_back(store, *wanted):
    """Each call's parts read back by the store, checked against the same lines read by hand
    and parsed straight from the proxy's files."""
    picked = chosen(store, *wanted)
    got = store.texts(picked)
    files = ProxyFiles(store.dir)
    assert got == [calls.parse(json.loads(by_hand(files, c.where)))[1] for c in picked]
    return got


class ProxyFiles:
    """The two paths ``by_hand`` reads, for a store's directory."""

    def __init__(self, root):
        self.segments, self.live = root / "segments", root / "requests.jsonl"


def test_chosen_calls_are_read_back_in_the_order_given(store):
    got = read_back(store, "b1", "a1")
    assert got[1] == (("request", SECRET), ("response", ANSWER))


def test_a_call_moved_live_then_pending_then_compressed_is_read_back(settings, proxy):
    proxy.append(batch("c", 2))
    store = Store(settings)
    store.refresh()
    pending = proxy.rotate()
    read_back(store, "b1", "c1")
    proxy.compress(pending)
    read_back(store, "a1", "b1", "c0", "c1")


def test_two_rotations_between_the_index_and_the_read(settings, proxy):
    store = Store(settings)
    store.refresh()
    proxy.compress(proxy.rotate())
    proxy.append(batch("c", 2))
    proxy.rotate()  # left pending
    proxy.append(batch("d", 1))
    read_back(store, "a1", "a2", "b1")


def test_a_session_across_a_segment_cut_and_the_live_file(settings, proxy):
    proxy.seal([entry("a3", "alice", 1_800_000_100.0, "q3", "r3", "s-alice")])
    proxy.append([entry("a4", "alice", 1_800_000_200.0, "q4", "r4", "s-alice")])
    store = Store(settings)
    store.refresh()
    session = next(s for s in calls.sessions(store.calls()) if s.id == "s-alice")
    assert [c.id for c in session.calls] == ["a1", "a2", "a3", "a4"]
    assert store.texts(list(session.calls)) == read_back(store, "a1", "a2", "a3", "a4")


def test_with_no_live_file_between_the_two_renames_the_pending_file_is_read(settings, proxy):
    store = Store(settings)
    store.refresh()
    proxy.segments.mkdir(exist_ok=True)
    os.replace(proxy.live, proxy.segments / "requests-000000003-000000003.jsonl")
    read_back(store, "b1")


def test_a_pending_file_compressed_between_the_listing_and_the_open(settings, proxy, monkeypatch):
    proxy.append(batch("c", 1))
    pending = proxy.rotate()
    store = Store(settings)
    store.refresh()
    real_list = Store._list

    def list_then_compress(self):
        listing = real_list(self)
        if pending.exists():
            proxy.compress(pending)
        return listing

    monkeypatch.setattr(Store, "_list", list_then_compress)
    read_back(store, "b1", "c0")


def test_a_listing_that_misses_a_file_once_is_looked_at_again(settings, proxy, monkeypatch):
    store = Store(settings)
    store.refresh()
    real_list, looks = Store._list, []

    def misses_once(self):
        looks.append(1)
        listing = real_list(self)
        return listing[1:] if len(looks) == 1 else listing  # a1 a2's file not seen once

    monkeypatch.setattr(Store, "_list", misses_once)
    read_back(store, "a1")
    assert len(looks) == 2


def test_a_file_gone_for_good_is_gone(settings, proxy):
    store = Store(settings)
    store.refresh()
    (proxy.segments / "requests-000000001-000000002.jsonl.zst").unlink()
    with pytest.raises(store_module.GoneError, match=r"seq 1 \(call a1\)") as gone:
        store.texts(chosen(store, "a1", "b1"))
    assert [c.id for c in gone.value.calls] == ["a1"]


def test_a_log_that_started_again_is_refused_and_the_next_read_takes_the_new_one(settings, proxy):
    store = Store(settings)
    store.refresh()
    zst = proxy.segments / "requests-000000001-000000002.jsonl.zst"
    other = [entry("x1", "mallory", 1_900_000_000.0, "another", "call"), SEALED[1]]
    zst.write_bytes(compress(lines(other)))  # seq 1 now names another call
    with pytest.raises(store_module.ChangedError, match=r"seq 1 \(call a1\) no longer reads"):
        store.texts(chosen(store, "a1"))
    store.refresh()  # the changed file is parsed again
    assert ids(store) == ["x1", "a2", "b1"]
    assert read_back(store, "x1")[0][0] == ("request", "another")


def test_a_file_that_no_longer_decompresses_is_refused(settings, proxy):
    store = Store(settings)
    store.refresh()
    zst = proxy.segments / "requests-000000001-000000002.jsonl.zst"
    zst.write_bytes(zst.read_bytes()[:20])
    with pytest.raises(store_module.ChangedError, match="no longer reads"):
        store.texts(chosen(store, "a2"))


def test_a_disk_error_is_a_store_error(store, monkeypatch):
    def denied(c):
        raise PermissionError("denied")

    monkeypatch.setattr(store_module, "_open", denied)
    with pytest.raises(StoreError, match="cannot be read: denied"):
        store.texts(chosen(store, "a1"))


def test_a_call_after_a_gap_or_a_failed_segment_is_read_back(settings, proxy):
    bad = proxy.seal(batch("c", 2))
    bad.write_bytes(bad.read_bytes()[:20])  # its calls are never indexed
    proxy.seal(batch("d", 1))
    store = Store(settings)
    store.refresh()
    assert "c0" not in ids(store) and "b1" not in ids(store)  # b1 was sealed with them
    read_back(store, "a2", "d0")


def test_a_repeated_id_reads_back_its_latest_copy(settings, proxy):
    proxy.append([entry("a1", "alice", 1_800_000_000.0, "a1 again, changed", "and answered")])
    store = Store(settings)
    store.refresh()
    assert read_back(store, "a1")[0][0] == ("request", "a1 again, changed")


def test_calls_left_from_the_old_live_file_are_read_from_its_pending_file(settings, proxy):
    store = Store(settings)
    store.refresh()
    proxy.segments.mkdir(exist_ok=True)
    os.replace(proxy.live, proxy.segments / "requests-000000003-000000003.jsonl")
    store.refresh()  # no live file: the old live calls stay next to the pending file's
    assert chosen(store, "b1")[0].where.seq == 3
    read_back(store, "a1", "b1")


def test_a_read_back_does_not_wait_for_a_refresh(store):
    done = []
    with store._refresh_lock:
        reader = threading.Thread(target=lambda: done.append(store.texts(chosen(store, "a1"))))
        reader.start()
        reader.join(timeout=5)
    assert done, "texts() waited for the refresh lock"


# ---- memory -------------------------------------------------------------------------------


def tool_call(i, size):
    """A call whose newest turn carries a tool result of ``size`` characters."""
    e = entry(f"m{i}", "mia", 1_800_010_000.0 + i, "", f"answer {i}", "s-mem")
    result = [{"type": "tool_result", "tool_use_id": "t", "content": f"{i} " + "x" * size}]
    e["request_raw"] = {
        "text": json.dumps({"model": "m", "messages": [{"role": "user", "content": result}]})
    }
    return e


def held_after_refresh(root, size, n=200) -> int:
    """Bytes still allocated once a store has indexed ``n`` calls with ``size``-character tool
    results: half sealed, half live."""
    proxy = ProxyDir(root)
    proxy.seal([tool_call(i, size) for i in range(n // 2)])
    proxy.append([tool_call(i, size) for i in range(n // 2, n)])
    store = Store(
        Settings(proxy_dir=root, boxes=(Target("dev", API_KEY, "b", "r"),), sync_seconds=300)
    )
    gc.collect()
    tracemalloc.start()
    try:
        store.refresh()
        gc.collect()
        held, _ = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(store.calls()) == n and store.calls()[0].content_bytes > size
    return held


def test_memory_held_does_not_grow_with_the_text(tmp_path):
    held_after_refresh(tmp_path / "warm", 10)  # imports and caches, once
    small = held_after_refresh(tmp_path / "small", 1_000)  # 200 KB of text
    large = held_after_refresh(tmp_path / "large", 50_000)  # 10 MB of text
    assert large < small * 1.1 + 20_000, (small, large)
    assert large / 200 < 2_000, f"{large / 200:.0f} bytes per call"
