import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from test_deployed import git

from server import app as web
from server import deployed
from server.config import load_settings


def test_two_boxes_keep_different_source_commits_when_production_changes(tmp_path):
    remote = tmp_path / "remote"
    remote.mkdir()
    git(remote, "init", "-b", "production")
    git(remote, "config", "user.name", "test")
    git(remote, "config", "user.email", "test@example.com")
    plan = remote / "plan"
    plan.mkdir()
    (plan / "plan.json").write_text('{"plan_id":"first"}')
    trust = remote / "static/aiab/baked/trust.json"
    trust.parent.mkdir(parents=True)
    trust.write_text('{"lab_key":"first-key"}')
    git(remote, "add", ".")
    git(remote, "commit", "-m", "first source")
    first = git(remote, "rev-parse", "HEAD")
    (plan / "plan.json").write_text('{"plan_id":"second"}')
    trust.write_text('{"lab_key":"second-key"}')
    git(remote, "commit", "-am", "second source")
    second = git(remote, "rev-parse", "HEAD")
    pins = remote / "static/deploy/tinfoil/audit"
    (pins / "boxes").mkdir(parents=True)
    (pins / "release.json").write_text(
        json.dumps(
            {"application_source_commit": first, "host": "first.example", "release_digest": "first"}
        )
    )
    (pins / "boxes/dev.json").write_text(
        json.dumps(
            {
                "application_source_commit": second,
                "host": "second.example",
                "release_digest": "second",
            }
        )
    )
    git(remote, "add", ".")
    git(remote, "commit", "-m", "two independent releases")
    data = tmp_path / "data"
    # Exercise real git snapshots, not a substituted cache implementation.
    deployed.snapshot(data, str(remote), "production")
    original = deployed.snapshot
    from unittest.mock import patch

    settings = replace(load_settings(), repo="owner/repo", run_plan_dir=data / "releases/old/plan")
    with patch.object(
        deployed, "snapshot", lambda d, url, branch: original(d, str(remote), branch)
    ):
        a = deployed.refresh(settings)
        b = deployed.refresh(settings, "dev")
        assert a.box_url == "https://first.example"
        assert b.box_url == "https://second.example"
        assert json.loads((a.run_plan_dir / "plan.json").read_text())["plan_id"] == "first"
        assert json.loads((b.run_plan_dir / "plan.json").read_text())["plan_id"] == "second"
        assert json.loads(a.run_trust_file.read_text())["lab_key"] == "first-key"
        assert json.loads(b.run_trust_file.read_text())["lab_key"] == "second-key"
        assert deployed.choices(b) == ["default", "dev"]
        # A later plan edit neither mutates in-flight settings nor redirects either box.
        (plan / "plan.json").write_text('{"plan_id":"third"}')
        git(remote, "commit", "-am", "third source")
        assert deployed.refresh(settings).run_plan_dir == a.run_plan_dir
        assert deployed.refresh(settings, "dev").run_plan_dir == b.run_plan_dir
        assert a.release_file.read_text() == deployed.refresh(settings).release_file.read_text()
        with pytest.raises(ValueError, match="not found"):
            deployed.refresh(settings, "missing")
        with pytest.raises(ValueError, match="Invalid box"):
            deployed.refresh(settings, "../../default")


def test_runs_post_keeps_selected_box_and_rejects_changed_release(monkeypatch):
    chosen = replace(load_settings(), box_name="dev", box_url="https://dev.example")
    monkeypatch.setattr(web, "user_of", lambda request: {"party": "lab", "username": "test"})
    selected = []

    def refresh(settings, box_name):
        selected.append(box_name)
        return chosen

    monkeypatch.setattr(deployed, "refresh", refresh)
    monkeypatch.setattr(
        web.runs,
        "preflight",
        lambda _: {"ok": True, "missing": [], "release": {"release_digest": "current"}},
    )
    monkeypatch.setattr(web.proxylogs, "load_records", lambda _: [])
    from types import SimpleNamespace

    monkeypatch.setattr(
        web.proxylogs, "sessions", lambda _: [SimpleNamespace(id="s1", requests=[])]
    )
    monkeypatch.setattr(web.proxylogs, "to_box_records", lambda _: [{"content": "test"}])
    started = []
    monkeypatch.setattr(
        web.runs, "start_run", lambda settings, *args, **kw: started.append(settings) or "run1"
    )
    client = TestClient(web.app)
    response = client.post(
        "/runs/start",
        data={"box": "dev", "session": "s1", "release_digest": "current"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/runs/run1"
    assert started == [chosen]
    response = client.post(
        "/runs/start",
        data={"box": "dev", "session": "s1", "release_digest": "stale"},
        follow_redirects=False,
    )
    assert (
        "release+changed" in response.headers["location"]
        or "release%20changed" in response.headers["location"]
    )
    assert started == [chosen]
    assert selected == ["dev", "dev"]


def test_box_selection_survives_form_submission_and_filters():
    rendered = web.templates.get_template("runs.html").render(
        pre={
            "release": {
                "host": "dev.example",
                "tag": "v1.0.0",
                "plan_digest": "sha256:test",
                "release_digest": "release1",
            },
            "ok": True,
        },
        boxes=["default", "dev"],
        box_name="dev",
        plan_digest="sha256:test",
        sessions=[],
        all_count=0,
        whos=[],
        win={"from": "", "to": "", "who": ""},
        totals={"records": 0, "kb": 0, "refused": 0},
        runs=[],
    )
    from html.parser import HTMLParser

    class Forms(HTMLParser):
        active = None
        values = {}

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "form":
                self.active = attrs.get("id")
            if tag == "input" and self.active == "run-form":
                self.values[attrs["name"]] = attrs.get("value")

        def handle_endtag(self, tag):
            if tag == "form":
                self.active = None

    forms = Forms()
    forms.feed(rendered)
    assert forms.values == {"box": "dev", "release_digest": "release1"}


def test_new_box_cannot_fall_back_to_legacy_default_deployment(tmp_path, monkeypatch):
    from server import publish

    calls = []

    def command(repo, *args, **kwargs):
        calls.append(args)
        if args[0] == "show":
            return "gh workflow run release.yml --ref production"  # legacy default-only workflow
        return ""

    monkeypatch.setattr(publish, "_git", command)
    result = publish.push_deploy(
        tmp_path,
        tmp_path / "plan",
        "panda",
        "sha256:" + "a" * 64,
        {},
        base="production",
        new_box="dev",
    )
    assert "does not support new boxes" in result.error
    assert not any(args[0] == "push" for args in calls)
