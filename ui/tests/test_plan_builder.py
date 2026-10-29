"""The plan builder's server side: loading and saving plan.json through the box's loader,
and the archive of agreements written for the previous plan format."""

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from server import editing
from server import plan_view as pv
from server.auth import Account
from server.config import load_settings

ACTOR = {"username": "author", "party": "lab"}


@pytest.fixture
def settings(tmp_path):
    return replace(load_settings(), agreements_dir=tmp_path / "agreements", push_drafts=False)


@pytest.fixture
def draft(settings):
    errors, aid = pv.new_agreement(
        settings, {"plan_id": "builder-test", "problem": "Does the agent leak secrets?"}, ACTOR
    )
    assert not errors
    return pv.for_agreement(settings, aid)


def plan_of(draft):
    return json.loads((draft.plan_dir / "plan.json").read_text())


def node(plan, node_id):
    return next(n for n in plan["nodes"] if n["id"] == node_id)


def client_for(monkeypatch, settings, user="author", party="lab"):
    from server import app as web

    monkeypatch.setattr(web, "settings", settings)
    client = TestClient(web.app)
    client.cookies.set(web.COOKIE, web.signer.issue(Account(user, party, "Author", "unused")))
    return web, client


def test_new_agreement_is_a_valid_plan_asking_the_question(draft):
    view = pv.load_plan_view(draft)
    assert view.issue is None
    assert view.plan_id == "builder-test"
    assert view.description == "Does the agent leak secrets?"
    assert "leak secrets" in node(view.plan, "classifier")["config"]["instructions"]
    assert [n.type for n in view.nodes] == ["input_logs", "classifier", "final_report"]
    assert pv.load_history(draft.plan_dir)[0]["kind"] == "created"


def test_save_plan_refuses_what_the_box_refuses_and_records_the_rest(draft):
    before = pv.load_plan_view(draft).digest
    plan = plan_of(draft)
    node(plan, "report")["inputs"]["result"] = "logs.records"
    digest, issues = pv.save_plan(draft, plan, ACTOR)
    assert digest is None
    assert issues[0]["node"] == "report" and "receives LogRecords" in issues[0]["message"]
    assert pv.load_plan_view(draft).digest == before  # nothing written
    plan = plan_of(draft)
    node(plan, "classifier")["config"]["threshold"] = 0.9
    digest, issues = pv.save_plan(draft, plan, ACTOR)
    assert digest and not issues and digest != before
    event = pv.load_history(draft.plan_dir)[0]
    assert event["kind"] == "plan" and '+        "threshold": 0.9' in event["detail"]
    assert (draft.plan_dir / "plan.json").read_bytes().endswith(b"}\n")  # canonical JSON
    assert pv.save_plan(draft, plan, ACTOR) == (digest, [])  # saving the same plan is a no-op
    assert pv.load_history(draft.plan_dir)[0] == event


def test_save_plan_keeps_the_folder_name_as_plan_id(draft):
    plan = plan_of(draft)
    plan["plan_id"] = "something-else"
    digest, issues = pv.save_plan(draft, plan, ACTOR)
    assert digest is None and "plan_id must stay builder-test" in issues[0]["message"]


def test_builder_endpoints_follow_login_lease_frozen_and_version(draft, monkeypatch):
    web, client = client_for(monkeypatch, draft)
    url = "/a/builder-test/plan.json"
    anonymous = TestClient(web.app)
    assert anonymous.get(url).status_code == 401
    assert anonymous.put(url, json={}).status_code == 401

    doc = client.get(url).json()
    assert doc["plan"]["plan_id"] == "builder-test" and doc["issue"] is None
    assert doc["editable"] is False  # nobody pressed Edit yet
    plan = doc["plan"]
    node(plan, "classifier")["config"]["threshold"] = 0.8

    # Saving needs the edit lease.
    response = client.put(url, json={"plan": plan, "expect": doc["digest"]})
    assert response.status_code == 409 and "Edit this plan" in response.json()["error"]
    assert client.post("/a/builder-test/plan/edit", follow_redirects=False).status_code == 303
    assert client.get(url).json()["editable"] is True

    # Wrong origin, wrong content type, stale version.
    bad_origin = client.put(
        url, json={"plan": plan}, headers={"Origin": "https://elsewhere.example"}
    )
    assert bad_origin.status_code == 403
    assert client.put(url, content="{}").status_code == 415
    stale = client.put(url, json={"plan": plan, "expect": "sha256:" + "0" * 64})
    assert stale.status_code == 409 and "changed" in stale.json()["error"]

    # A plan the box refuses comes back with the loader's issues and is not saved.
    broken = json.loads(json.dumps(plan))
    node(broken, "report")["inputs"]["result"] = "logs.records"
    refused = client.put(url, json={"plan": broken, "expect": doc["digest"]})
    assert refused.status_code == 422
    assert refused.json()["issues"][0]["node"] == "report"
    assert client.get(url).json()["digest"] == doc["digest"]

    saved = client.put(url, json={"plan": plan, "expect": doc["digest"]})
    assert saved.status_code == 200
    assert saved.json()["digest"] != doc["digest"]
    assert node(plan_of(draft), "classifier")["config"]["threshold"] == 0.8

    # Another person cannot save while the lease is held.
    other = TestClient(web.app)
    other.cookies.set(web.COOKIE, web.signer.issue(Account("them", "auditor", "Them", "unused")))
    response = other.put(url, json={"plan": plan, "expect": saved.json()["digest"]})
    assert response.status_code == 409 and "is editing" in response.json()["error"]
    editing.clear("builder-test")

    # The deploy slot is read only.
    assert client.put("/a/plan/plan.json", json={"plan": plan}).status_code == 403


def test_plan_page_embeds_the_builder_and_lists_the_steps(draft, monkeypatch):
    web, client = client_for(monkeypatch, draft)
    page = client.get("/a/builder-test/plan")
    assert page.status_code == 200
    assert "/audit-plan-playground/?agreement=builder-test&amp;readonly=1" in page.text
    assert "Does the agent leak secrets?" in page.text
    assert "Classifier" in page.text and "releases 2 fields" in page.text
    editing_page = client.get("/a/builder-test/plan?edit=1")
    assert '/audit-plan-playground/?agreement=builder-test"' in editing_page.text
    editing.clear("builder-test")


def test_old_format_agreements_are_archived_not_listed_and_can_be_deleted(settings, monkeypatch):
    old = settings.agreements_dir / "bio-demo"
    (old / "prompts").mkdir(parents=True)
    (old / "plan.py").write_text("def build_query(logs, *, window):\n    return []\n")
    (old / "plan.json").write_text(
        json.dumps({"schema": "aiab-baked-plan/v1", "plan_id": "bio-demo-001"})
    )
    (old / "prompts" / "classifier_v1.txt").write_text("prompt")
    (settings.agreements_dir / "notes.txt").write_text("not a folder")
    errors, _ = pv.new_agreement(settings, {"plan_id": "current", "problem": "q"}, ACTOR)
    assert not errors

    moved = pv.archive_old_agreements(settings)
    assert moved == ["bio-demo"]
    assert not old.exists()
    assert (settings.agreements_dir / "_archive" / "bio-demo" / "plan.py").is_file()
    assert pv.archive_old_agreements(settings) == []  # idempotent
    assert [r["id"] for r in pv.list_agreements(settings)] == ["current"]
    archived = pv.list_archived(settings)
    assert archived == [
        {"id": "bio-demo", "plan_id": "bio-demo-001", "schema": "aiab-baked-plan/v1", "files": 3}
    ]
    # The archive folder is not an agreement, whatever the URL says.
    assert pv.for_agreement(settings, "_archive") is None

    web, client = client_for(monkeypatch, settings)
    page = client.get("/agreements")
    assert "Archived · 1 agreement in the previous plan format" in page.text
    assert "bio-demo-001" in page.text
    assert client.get("/a/_archive/plan", follow_redirects=False).status_code == 303
    assert client.post("/archive/bio-demo/delete", follow_redirects=False).status_code == 303
    assert not (settings.agreements_dir / "_archive" / "bio-demo").exists()
    assert (
        client.post("/archive/missing/delete", follow_redirects=False)
        .headers["location"]
        .startswith("/agreements?err=")
    )


def test_archiving_twice_under_one_name_keeps_both(settings):
    for content in ("one", "two"):
        old = settings.agreements_dir / "dup"
        old.mkdir(parents=True)
        (old / "plan.py").write_text(content)
        pv.archive_old_agreements(settings)
    names = sorted(p.name for p in (settings.agreements_dir / "_archive").iterdir())
    assert names[0] == "dup" and names[1].startswith("dup-") and len(names) == 2


def test_copying_an_old_format_deploy_slot_is_refused(settings, tmp_path):
    slot = tmp_path / "old-plan"
    slot.mkdir()
    (slot / "plan.py").write_text("def build_query(logs, *, window):\n    return []\n")
    (slot / "plan.json").write_text(json.dumps({"schema": "aiab-baked-plan/v1", "plan_id": "old"}))
    old_slot = replace(settings, plan_dir=slot)
    errors, created = pv.new_agreement(
        old_slot, {"plan_id": "from-old", "problem": "q", "source": "copy"}, ACTOR
    )
    assert created is None and any("previous format" in e for e in errors)
    assert not (settings.agreements_dir / "from-old").exists()
    errors, created = pv.new_agreement(
        old_slot, {"plan_id": "from-template", "problem": "q", "source": "template"}, ACTOR
    )
    assert not errors and pv.load_plan_view(pv.for_agreement(old_slot, created)).issue is None
