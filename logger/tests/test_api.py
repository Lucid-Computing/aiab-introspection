import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from aiab.baked import records as box_records
from aiab.baked.sealing import SCHEME
from conftest import ANSWER, LIVE, PROD_KEY, SEALED, SECRET, compress, entry, lines

from logserver import calls


def test_every_route_but_health_needs_the_key(client):
    assert client.get("/healthz").json() == {"ok": True}
    for method, path in [
        ("GET", "/v1/sessions"),
        ("GET", "/v1/status"),
        ("POST", "/v1/sync"),
        ("POST", "/v1/export"),
    ]:
        for headers in ({"x-log-key": ""}, {"x-log-key": "wrong"}):
            assert client.request(method, path, headers=headers).status_code == 401, path


def test_sessions_are_metadata_and_never_what_was_said(client):
    response = client.get("/v1/sessions")
    body = response.json()
    assert body["whos"] == ["alice", "bob"]
    alice = next(s for s in body["sessions"] if s["id"] == "s-alice")
    assert alice == {
        "id": "s-alice",
        "who": "alice",
        "started": "2027-01-15T08:00:00Z",
        "ended": "2027-01-15T08:01:00Z",
        "model": "claude-haiku-5-5",
        "records": 4,
        "content_bytes": 92,
        "calls": [
            {
                "id": "a1",
                "ts": "2027-01-15T08:00:00Z",
                "who": "alice",
                "status": 200,
                "duration_s": 1.5,
                "model": "claude-haiku-5-5",
                "stop_reason": "end_turn",
                "request_bytes": alice["calls"][0]["request_bytes"],
                "messages": 1,
                "records": 2,
                "kinds": ["request", "response"],
                "content_bytes": len(SECRET) + len(ANSWER),
            },
            alice["calls"][1],
        ],
    }
    assert SECRET not in response.text and ANSWER not in response.text
    assert [s["who"] for s in client.get("/v1/sessions?who=bob").json()["sessions"]] == ["bob"]


def test_an_export_is_sealed_and_only_the_box_opens_it(client, store, fakebox):
    response = client.post("/v1/export", json={"sessions": ["s-alice"]})
    assert response.status_code == 200
    body = response.json()
    assert SECRET not in response.text and ANSWER not in response.text
    assert body["scheme"] == SCHEME and body["boot_id"] == fakebox.boot_id
    assert body["box"] == "box.example" and body["sessions"] == ["s-alice"]
    sealed = body["records"]
    alice = list(calls.sessions(list(store.calls()))[1].calls)
    plain = calls.box_records(list(zip(alice, store.texts(alice), strict=True)))
    # Metadata stays readable, for the UI's scope checks; the content does not.
    for s, p in zip(sealed, plain, strict=True):
        assert {k: s[k] for k in p if k != "content"} == {k: p[k] for k in p if k != "content"}
        assert s["content"] != p["content"] and s["boot_id"] == fakebox.boot_id and s["enc"]
    assert fakebox.decryptor.unseal(sealed, fakebox.boot_id) == plain


def test_the_ui_can_sign_the_sealed_records_as_they_are(client):
    """The export statement the box checks is made over the sealed records, so the
    agreement UI signs what it received without being able to read it."""
    sealed = client.post("/v1/export", json={"sessions": ["s-alice", "bob-1800000500"]}).json()
    statement = box_records.export_statement(
        sealed["records"], box_records.scope_of(sealed["records"])
    )
    assert statement["record_count"] == 6
    assert [r["offset"] for r in sealed["records"]] == list(range(6))


def test_the_caller_cannot_choose_the_key_or_the_box(client):
    for extra in ({"encryption_key": "AAAA"}, {"box": "evil.example"}, {"boot_id": "x"}):
        response = client.post("/v1/export", json={"sessions": ["s-alice"], **extra})
        assert response.status_code == 422, extra


def test_unknown_or_missing_sessions_are_refused(client):
    response = client.post("/v1/export", json={"sessions": ["s-alice", "nope"]})
    assert response.status_code == 404
    assert response.json()["detail"] == {"error": "unknown_sessions", "sessions": ["nope"]}
    assert client.post("/v1/export", json={"sessions": []}).status_code == 422
    assert client.post("/v1/export", json={}).status_code == 422


def test_a_box_that_restarted_is_attested_again_and_gets_its_new_key(client, fakebox):
    first = client.post("/v1/export", json={"sessions": ["s-alice"]}).json()
    client.post("/v1/export", json={"sessions": ["s-alice"]})
    assert fakebox.attested == 1  # the pinned connection is kept between exports
    old = fakebox.decryptor
    fakebox.restart()
    second = client.post("/v1/export", json={"sessions": ["s-alice"]}).json()
    assert fakebox.attested == 2
    assert second["boot_id"] == fakebox.boot_id != first["boot_id"]
    assert fakebox.decryptor.unseal(second["records"], fakebox.boot_id)[0]["content"] == SECRET
    assert old is not fakebox.decryptor


def test_no_box_means_no_export_and_no_plaintext(client, fakebox):
    fakebox.down = True
    response = client.post("/v1/export", json={"sessions": ["s-alice"]})
    assert response.status_code == 502
    assert SECRET not in response.text and "records" not in json.loads(response.text)


def test_sync_reads_again_and_status_tells_how_it_went(client, proxy):
    proxy.append([{"id": "c1"}])  # parses as a call with no request: one more call
    proxy.append(raw=b"not json\n")
    assert client.post("/v1/sync").json() == {"new": 0, "segments": 1, "live": 3, "calls": 4}
    status = client.get("/v1/status").json()
    assert status["calls"] == 4 and status["error"] is None and status["box"] == "box.example"
    assert status["result"]["live"] == 3 and status["at"]
    assert status["problems"] == [{"file": "requests.jsonl", "problem": "1 lines are not calls"}]


def test_status_and_sessions_answer_while_a_read_is_running(client, store):
    with store._refresh_lock:  # a long read in progress
        assert client.get("/v1/status").json()["calls"] == 3
        assert len(client.get("/v1/sessions").json()["sessions"]) == 2


def test_an_unexpected_read_error_is_a_503_too(client, store, monkeypatch):
    def refuse(*args):
        raise PermissionError("permission denied")

    monkeypatch.setattr(store, "_snapshot", refuse)
    response = client.post("/v1/sync")
    assert response.status_code == 503 and "permission denied" in response.json()["detail"]
    assert client.get("/v1/status").json()["error"]


def test_an_unreadable_proxy_directory_is_a_503_and_keeps_the_index(client, settings):
    os.rename(settings.proxy_dir, settings.proxy_dir.with_name("moved"))
    response = client.post("/v1/sync")
    assert response.status_code == 503 and "missing" in response.json()["detail"]
    status = client.get("/v1/status").json()
    assert status["error"] and status["calls"] == 3
    assert len(client.get("/v1/sessions").json()["sessions"]) == 2


def test_each_key_gets_its_own_box_and_no_other(client, fakebox, prodbox):
    """Two UIs, two boxes: the key decides the box, and only that box opens the export."""
    dev = client.post("/v1/export", json={"sessions": ["s-alice"]}).json()
    prod = client.post(
        "/v1/export", json={"sessions": ["s-alice"]}, headers={"x-log-key": PROD_KEY}
    ).json()
    assert dev["box"] == "box.example" and dev["boot_id"] == fakebox.boot_id
    assert prod["box"] == "prod.example" and prod["boot_id"] == prodbox.boot_id
    assert prodbox.decryptor.unseal(prod["records"], prodbox.boot_id)[0]["content"] == SECRET
    with pytest.raises(Exception):  # noqa: B017, PT011 - the dev box cannot open it
        fakebox.decryptor.unseal(prod["records"], prodbox.boot_id)
    status = client.get("/v1/status", headers={"x-log-key": PROD_KEY}).json()
    assert status["box"] == "prod.example"


def test_one_box_down_does_not_stop_the_other(client, prodbox):
    prodbox.down = True
    assert client.post("/v1/export", json={"sessions": ["s-alice"]}).status_code == 200
    response = client.post(
        "/v1/export", json={"sessions": ["s-alice"]}, headers={"x-log-key": PROD_KEY}
    )
    assert response.status_code == 502


def test_an_export_can_start_its_numbering_later_and_long_records_stay_whole(client, proxy):
    """A run joining two servers' exports gives each its own range of offsets."""
    first = client.post("/v1/export", json={"sessions": ["s-alice"]}).json()["records"]
    later = client.post("/v1/export", json={"sessions": ["s-alice"], "offset": 40}).json()
    assert [r["offset"] for r in later["records"]] == [40 + r["offset"] for r in first]
    assert (
        client.post("/v1/export", json={"sessions": ["s-alice"], "offset": -1}).status_code == 422
    )


def test_a_long_record_is_sealed_whole(client, proxy, fakebox):
    long = "x" * 30_000
    proxy.append([entry("long1", "carol", 500, long, "ok", session="s-long")])
    client.post("/v1/sync")
    sealed = client.post("/v1/export", json={"sessions": ["s-long"]}).json()["records"]
    assert fakebox.decryptor.unseal(sealed, fakebox.boot_id)[0]["content"] == long


# ---- the index keeps no text; an export reads it back ------------------------------------


def all_sessions(client):
    return [s["id"] for s in client.get("/v1/sessions").json()["sessions"]]


def test_the_index_holds_no_text(store):
    for call in store.calls():
        assert not hasattr(call, "parts") and not hasattr(call, "__dict__")  # slots, no text
        assert SECRET not in repr(call) and ANSWER not in repr(call)


def test_an_export_equals_the_records_built_straight_from_what_the_proxy_wrote(
    client, proxy, fakebox
):
    """Sealed, pending and live sessions, decrypted, against records built from the entries
    themselves without the store."""
    pending = [entry("p1", "dan", 1_800_000_700.0, "pending question", "pending answer", "s-dan")]
    proxy.append(pending)
    proxy.rotate()
    live = [entry("l1", "dan", 1_800_000_800.0, "live question", "live answer", "s-dan")]
    proxy.append(live)
    client.post("/v1/sync")
    wanted = all_sessions(client)
    sealed = client.post("/v1/export", json={"sessions": wanted}).json()["records"]
    written = SEALED + LIVE + pending + live
    parsed = {c.id: (c, parts) for c, parts in map(calls.parse, written)}
    grouped = {s.id: s for s in calls.sessions([c for c, _ in parsed.values()])}
    expected = calls.box_records([(c, parsed[c.id][1]) for i in wanted for c in grouped[i].calls])
    assert fakebox.decryptor.unseal(sealed, fakebox.boot_id) == expected


def test_a_session_whose_file_is_gone_is_listed_but_not_exported(client, proxy):
    (proxy.segments / "requests-000000001-000000002.jsonl.zst").unlink()
    client.post("/v1/sync")
    assert "s-alice" in all_sessions(client)
    response = client.post("/v1/export", json={"sessions": ["s-alice", "bob-1800000500"]})
    assert response.status_code == 410
    assert response.json()["detail"].startswith("s-alice: no file holds seq 1 (call a1)")
    assert client.post("/v1/export", json={"sessions": ["bob-1800000500"]}).status_code == 200


def test_a_changed_file_is_refused_until_it_is_listed_again(client, proxy, fakebox):
    zst = proxy.segments / "requests-000000001-000000002.jsonl.zst"
    changed = [
        entry("a1", "alice", 1_800_000_000.0, "rewritten", "after listing", "s-alice"),
        SEALED[1],
    ]
    zst.write_bytes(compress(lines(changed)))
    response = client.post("/v1/export", json={"sessions": ["s-alice"]})
    assert response.status_code == 409 and "list again" in response.json()["detail"]
    assert "rewritten" not in response.text
    client.post("/v1/sync")  # listing again parses the changed file again
    sealed = client.post("/v1/export", json={"sessions": ["s-alice"]}).json()["records"]
    assert fakebox.decryptor.unseal(sealed, fakebox.boot_id)[0]["content"] == "rewritten"


def test_nothing_is_read_for_a_session_with_no_text_or_a_box_that_is_down(
    client, proxy, store, fakebox, monkeypatch
):
    reads = []
    real = store.texts
    monkeypatch.setattr(store, "texts", lambda chosen: reads.append(chosen) or real(chosen))
    proxy.append([entry("e1", "erin", 1_800_000_900.0, "", "", "s-empty")])
    client.post("/v1/sync")
    assert client.post("/v1/export", json={"sessions": ["s-empty"]}).status_code == 422
    fakebox.down = True
    assert client.post("/v1/export", json={"sessions": ["s-alice"]}).status_code == 502
    assert reads == []


def test_an_export_does_not_wait_for_a_refresh(client, store):
    codes = []
    with store._refresh_lock:
        export = threading.Thread(
            target=lambda: codes.append(
                client.post("/v1/export", json={"sessions": ["s-alice"]}).status_code
            )
        )
        export.start()
        export.join(timeout=10)
    assert codes == [200], "the export waited for the refresh lock"


def test_a_disk_error_during_an_export_is_a_503(client, store, monkeypatch):
    def denied(self, problems):
        raise PermissionError("denied")

    monkeypatch.setattr(type(store), "_snapshot", denied)
    response = client.post("/v1/export", json={"sessions": ["s-alice"]})
    assert response.status_code == 503 and "denied" in response.json()["detail"]


def test_two_exports_at_once_both_get_the_same_records(client, fakebox):
    with ThreadPoolExecutor(2) as pool:
        answers = list(
            pool.map(lambda _: client.post("/v1/export", json={"sessions": ["s-alice"]}), (1, 2))
        )
    assert [a.status_code for a in answers] == [200, 200]
    opened = [fakebox.decryptor.unseal(a.json()["records"], fakebox.boot_id) for a in answers]
    assert opened[0] == opened[1] and opened[0][0]["content"] == SECRET


def test_calls_at_the_same_time_keep_their_order():
    first, _ = calls.parse(entry("t1", "tia", 1_800_005_000.0, "first", "one"))
    second, _ = calls.parse(entry("t2", "tia", 1_800_005_000.0, "second", "two"))
    pairs = [(first, (("request", "first"),)), (second, (("request", "second"),))]
    assert [r["content"] for r in calls.box_records(pairs)] == ["first", "second"]
    assert [r["content"] for r in calls.box_records(pairs[::-1])] == ["second", "first"]


def test_a_live_file_that_will_not_open_on_every_look_is_a_503(client, store, monkeypatch):
    real = type(store)._snapshot

    def no_live_file(self, problems):
        handle, listing = real(self, problems)
        handle.close()
        problems.append({"file": "requests.jsonl", "problem": "cannot be opened: denied"})
        return None, listing

    monkeypatch.setattr(type(store), "_snapshot", no_live_file)
    response = client.post("/v1/export", json={"sessions": ["bob-1800000500"]})
    assert response.status_code == 503 and "cannot be opened: denied" in response.json()["detail"]
