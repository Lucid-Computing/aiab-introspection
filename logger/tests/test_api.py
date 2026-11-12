import json
import os

from aiab.baked import records as box_records
from aiab.baked.sealing import SCHEME
from conftest import ANSWER, SECRET

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
    plain = calls.box_records(list(calls.sessions(list(store.calls()))[1].calls))
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
