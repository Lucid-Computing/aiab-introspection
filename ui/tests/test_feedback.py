"""The feedback rules on the server: who may delete a note, what a deleted note leaves
behind, which entries are mailed and what the email says. Nothing here sends mail:
``feedback._send`` is replaced in every test that sets email up."""

import json
import threading
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from server import app as ui
from server import feedback
from server.config import load_settings

LAB = {"username": "lab", "party": "lab", "display_name": "Lab"}
CHAN = {"username": "chan", "party": "auditor", "display_name": "Chan"}
SAM = {"username": "sam", "party": "auditor", "display_name": "Sam"}
EMAIL = {
    "feedback_email_to": "channel@example.invalid",
    "feedback_email_from": "aiab@example.invalid",
    "smtp_host": "smtp.example.invalid",
    "smtp_user": "chan@example.invalid",
    "smtp_password": "not-a-password",
}


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A client whose signed-in user is ``site.who`` (change it to switch accounts), with
    feedback in tmp_path and email off. ``site.configure(**fields)`` changes settings."""

    class Site:
        who = CHAN
        settings = replace(
            load_settings(),
            feedback_dir=tmp_path / "feedback",
            feedback_email_to=None,
            feedback_email_from=None,
            smtp_host=None,
            smtp_user=None,
            smtp_password=None,
        )

        def configure(self, **fields):
            self.settings = replace(self.settings, **fields)
            monkeypatch.setattr(ui, "settings", self.settings)

    s = Site()
    monkeypatch.setattr(ui, "settings", s.settings)
    monkeypatch.setattr(ui, "user_of", lambda request: s.who)
    s.client = TestClient(ui.app, follow_redirects=False)
    return s


@pytest.fixture
def outbox(monkeypatch):
    """Messages ``_send`` would have mailed, and the timers ``notify`` started, so a test
    can join them instead of sleeping."""
    sent, timers = [], []
    real_timer = threading.Timer

    def timer(*args, **kwargs):
        t = real_timer(*args, **kwargs)
        timers.append(t)
        return t

    monkeypatch.setattr(feedback.threading, "Timer", timer)
    monkeypatch.setattr(feedback, "_send", lambda settings, message: sent.append(message))

    class Outbox:
        messages = sent

        def wait(self):
            for t in timers:
                t.join(timeout=10)
                assert not t.is_alive(), "an email timer did not finish"

    return Outbox()


def _note(site, text="Check this step", page="/a/bio/sign", **extra):
    response = site.client.post("/notes", json={"text": text, "page": page, **extra})
    assert response.status_code == 200, response.text
    return response.json()["note"]


def _lines(site):
    path = site.settings.feedback_dir / "notes.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_only_the_author_can_delete_a_note(site):
    note = _note(site)
    assert note["mine"] is True
    assert site.client.get("/notes", params={"page": "/a/bio/sign"}).json()["notes"][0]["mine"]

    site.who = SAM
    [seen] = site.client.get("/notes", params={"page": "/a/bio/sign"}).json()["notes"]
    assert seen["id"] == note["id"] and seen["mine"] is False
    assert site.client.post(f"/notes/{note['id']}/delete").status_code == 403

    site.who = CHAN
    assert site.client.post(f"/notes/{note['id']}/delete").status_code == 200
    assert site.client.post(f"/notes/{note['id']}/delete").status_code == 404


def test_a_deleted_note_is_hidden_but_kept_in_the_file(site):
    gone = _note(site, text="Typo, please ignore")
    kept = _note(site, text="This one stays")
    assert site.client.post(f"/notes/{gone['id']}/delete").status_code == 200

    listed = site.client.get("/notes", params={"page": "/a/bio/sign"}).json()["notes"]
    assert [n["id"] for n in listed] == [kept["id"]]

    site.who = LAB
    html = site.client.get("/feedback").text
    assert "This one stays" in html and "Typo, please ignore" not in html

    lines = _lines(site)
    at = next(i for i, e in enumerate(lines) if e.get("id") == gone["id"])
    assert lines[at]["text"] == "Typo, please ignore"
    assert any(e["kind"] == "delete" and e["note"] == gone["id"] for e in lines[at + 1 :])


def test_a_note_deleted_before_the_delay_is_not_mailed(site, outbox):
    site.configure(**EMAIL, feedback_note_delay=0.5)
    gone = _note(site, text="Deleted in time")
    _note(site, text="Kept note")
    assert site.client.post(f"/notes/{gone['id']}/delete").status_code == 200
    form = site.client.post("/feedback", data={"text": "Form entry", "page": "/runs"})
    assert form.status_code == 303

    outbox.wait()
    bodies = [m.get_content() for m in outbox.messages]
    assert len(bodies) == 2
    assert any("Kept note" in b for b in bodies) and any("Form entry" in b for b in bodies)
    assert not any("Deleted in time" in b for b in bodies)


def test_email_names_the_author_and_links_to_the_page(site, outbox):
    site.configure(**EMAIL, feedback_note_delay=0)
    site.client.post(
        "/feedback",
        data={"text": "Sign button unclear", "name": "Chan Lee", "page": "/a/bio/sign?x=1&msg=Hi"},
        headers={"host": "aiab.example.invalid", "x-forwarded-proto": "https"},
    )
    outbox.wait()
    [form] = outbox.messages
    assert form["Subject"] == "Feedback from chan (Chan Lee) on /a/bio/sign?x=1"
    assert form["To"] == "channel@example.invalid"
    assert form["From"] == "aiab@example.invalid"
    body = form.get_content()
    assert "Sign button unclear" in body
    assert "https://aiab.example.invalid/a/bio/sign?x=1\n" in body

    note = _note(site, text="Margin note", page="/runs?err=oops", name="Chan Lee")
    outbox.wait()
    message = outbox.messages[1]
    assert message["Subject"] == "Note from chan (Chan Lee) on /runs"
    assert f"http://testserver/runs#note={note['id']}" in message.get_content()

    for hostile in ("//evil.example", "https://evil.example", "/\\evil.example"):
        assert feedback.clean_page(hostile) == "/"
        response = site.client.post("/feedback", data={"text": "x", "page": hostile})
        assert response.headers["location"].startswith("/?msg=")
    outbox.wait()
    assert [m["Subject"] for m in outbox.messages[2:]] == ["Feedback from chan on /"] * 3


def test_without_email_nothing_is_sent_and_margin_fields_round_trip(site, monkeypatch):
    def no_send(settings, message):
        raise AssertionError("_send called with email off")

    monkeypatch.setattr(feedback, "_send", no_send)
    started = []
    monkeypatch.setattr(feedback.threading, "Timer", lambda *a, **k: started.append(a))

    assert site.client.post("/feedback", data={"text": "hi", "page": "/runs"}).status_code == 303
    left = _note(site, side="left", gap=120.5, anchor="step-1", x=0.3)
    right = _note(site, side="right", gap=99999)
    neither = _note(site, side="top", gap=50, anchor="step-1")
    below = _note(site, side="left", gap=-5)
    assert started == []

    assert (left["side"], left["gap"], left["anchor"]) == ("left", 120.5, "")
    assert (right["side"], right["gap"]) == ("right", 4000.0)
    assert (neither["side"], neither["gap"], neither["anchor"]) == ("", 0.0, "step-1")
    assert (below["side"], below["gap"]) == ("left", 0.0)

    listed = {
        n["id"]: (n["side"], n["gap"])
        for n in site.client.get("/notes", params={"page": "/a/bio/sign"}).json()["notes"]
    }
    assert listed[left["id"]] == ("left", 120.5)
    assert listed[right["id"]] == ("right", 4000.0)
    assert listed[neither["id"]] == ("", 0.0)
