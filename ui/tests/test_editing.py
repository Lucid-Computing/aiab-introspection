"""Leaving the editing pages, signing or deploying ends an edit lease, so a stale
"X is editing" never blocks Deploy."""

from server import app as ui
from server import editing

LAB = {"username": "lab", "party": "lab"}
AUDITOR = {"username": "auditor", "party": "auditor"}


def setup_function():
    editing._leases.clear()


def test_opening_the_plan_outside_edit_mode_releases_the_lease(monkeypatch):
    monkeypatch.setattr(ui, "user_of", lambda request: LAB)
    assert ui._edit_mode(None, "a1", True) == (True, None)
    assert editing.holder("a1")["user"] == "lab"
    assert ui._edit_mode(None, "a1", False) == (False, None)
    assert editing.holder("a1") is None


def test_another_holder_still_shows_and_blocks(monkeypatch):
    editing.acquire("a1", AUDITOR)
    monkeypatch.setattr(ui, "user_of", lambda request: LAB)
    edit, other = ui._edit_mode(None, "a1", False)
    assert not edit and other["user"] == "auditor"
    edit, other = ui._edit_mode(None, "a1", True)
    assert not edit and other["user"] == "auditor"
    assert editing.holder("a1")["user"] == "auditor"


def test_release_all_drops_only_the_actors_leases():
    editing.acquire("a1", LAB)
    editing.acquire("a2", LAB)
    editing.acquire("a3", AUDITOR)
    editing.release_all(LAB)
    assert editing.holder("a1") is None and editing.holder("a2") is None
    assert editing.holder("a3")["user"] == "auditor"


def test_clear_drops_any_holder():
    editing.acquire("a1", AUDITOR)
    editing.clear("a1")
    assert editing.holder("a1") is None
    editing.clear("a1")  # nothing to drop is fine
