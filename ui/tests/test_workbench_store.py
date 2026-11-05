from concurrent.futures import ThreadPoolExecutor

import pytest

from server.workbench_store import Conflict, WorkbenchStore


def test_owner_isolation_immutable_datasets_and_revision_cas(tmp_path):
    store = WorkbenchStore(tmp_path / "workbench.db")
    original = {"id": "plan", "question": "original"}
    store.save_plan("alice", original, "v1", create=True)
    with pytest.raises(KeyError):
        store.plan("bob", "plan")
    assert store.plans("bob") == []
    changed = dict(original, question="changed")
    store.save_plan("alice", changed, "v2", expected_digest="v1")
    with pytest.raises(Conflict):
        store.save_plan("alice", original, "v3", expected_digest="v1")
    assert store.plan("alice", "plan")["digest"] == "v2"
    store.add_dataset("alice", {"id": "input", "events": [{"secret": "private"}]})
    with pytest.raises(Conflict):
        store.add_dataset("alice", {"id": "input", "events": []})
    with pytest.raises(KeyError):
        store.dataset("bob", "input")
    assert store.datasets("alice") == [{"id": "input"}]


def test_atomic_worker_claim_cancellation_and_restart(tmp_path):
    path = tmp_path / "workbench.db"
    store = WorkbenchStore(path)
    run = store.enqueue("alice", {"id": "p"}, "v1", "d", "rehearse")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.claim("alice", run), range(8)))
    assert sum(results) == 1
    with pytest.raises(KeyError):
        store.cancel("bob", run)
    assert store.cancel("alice", run)
    assert not store.finish("alice", run, result={"private": True})
    assert store.job("alice", run)["result"] is None
    pending = store.enqueue("alice", {"id": "p"}, "v1", "d", "rehearse")
    running = store.enqueue("alice", {"id": "p"}, "v1", "d", "rehearse")
    assert store.claim("alice", running)
    restarted = WorkbenchStore(path)
    assert restarted.recover() == 2
    assert restarted.job("alice", pending)["status"] == "interrupted"
    assert not restarted.claim("alice", running)
    assert restarted.job("alice", run)["status"] == "cancelled"


def test_failed_version_transition_is_not_saved_or_called_for_stale_editor(tmp_path):
    store = WorkbenchStore(tmp_path / "workbench.db")
    plan = {"id": "p", "question": "original"}
    store.save_plan("owner", plan, "v1", create=True)
    calls = []

    def transition():
        calls.append("attempted")
        raise ValueError("governance denied supersession")

    with pytest.raises(Conflict):
        store.save_plan("owner", plan, "v2", expected_digest="stale", before_commit=transition)
    assert calls == []
    with pytest.raises(ValueError, match="governance"):
        store.save_plan("owner", plan, "v2", expected_digest="v1", before_commit=transition)
    assert calls == ["attempted"]
    assert WorkbenchStore(store.path).plan("owner", "p")["digest"] == "v1"
    with pytest.raises(KeyError):
        store.revision("owner", "p", "v2")
    with pytest.raises(KeyError):
        store.revision("outsider", "p", "v1")
