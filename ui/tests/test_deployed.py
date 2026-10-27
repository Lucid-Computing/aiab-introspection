import json
import subprocess
from dataclasses import replace
from pathlib import Path

from server.config import load_settings
from server.deployed import snapshot
from server.runs import preflight


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def test_snapshot_updates_without_touching_dirty_drafts(tmp_path):
    remote = tmp_path / "remote"
    remote.mkdir()
    git(remote, "init", "-b", "production")
    git(remote, "config", "user.name", "test")
    git(remote, "config", "user.email", "test@example.com")
    (remote / "version").write_text("one")
    git(remote, "add", ".")
    git(remote, "commit", "-m", "first")
    data = tmp_path / "data"
    draft = data / "repo" / "plan"
    draft.mkdir(parents=True)
    (draft / "plan.py").write_text("unsaved draft")
    first = snapshot(data, str(remote), "production")
    assert (first / "version").read_text() == "one"
    (remote / "version").write_text("two")
    git(remote, "commit", "-am", "second")
    second = snapshot(data, str(remote), "production")
    assert (second / "version").read_text() == "two"
    assert (first / "version").read_text() == "one"
    assert (draft / "plan.py").read_text() == "unsaved draft"
    assert snapshot(data, str(remote), "production") == second


def test_run_uses_signed_production_not_unsigned_draft(tmp_path, monkeypatch):
    # The frozen demo plan stands in for a released production plan: the repository's own
    # plan/ and release pins change with every deploy and are not test data.
    root = Path(__file__).resolve().parents[2]
    fixture = root / "static/tests/fixtures/demo-plan"
    pins = json.loads((root / "static/deploy/tinfoil/audit/release.json").read_text())
    from aiab.baked.bundle import bundle_files, file_digest

    pins["plan_digest"] = file_digest(bundle_files(fixture))
    (tmp_path / "pins.json").write_text(json.dumps(pins))
    monkeypatch.setenv("AIAB_PLAN_DIR", str(tmp_path / "unsigned"))
    monkeypatch.setenv("AIAB_RUN_PLAN_DIR", str(fixture))
    monkeypatch.setenv("AIAB_TRUST_FILE", str(fixture / "trust.json"))
    monkeypatch.setenv("AIAB_RELEASE_FILE", str(tmp_path / "pins.json"))
    monkeypatch.delenv("AIAB_BOX_URL", raising=False)
    settings = load_settings()
    for name in ("lab.token", "logger.key"):
        (tmp_path / name).write_text("test-only")
    settings = replace(settings, demo_keys_dir=tmp_path)
    result = preflight(settings)
    assert result["ok"], result
    assert settings.box_url == "https://" + result["release"]["host"]
    invalid = preflight(replace(settings, run_plan_dir=tmp_path / "missing"))
    assert not invalid["ok"]
    assert any("signed deployed plan" in error for error in invalid["missing"])
    release = tmp_path / "release.json"
    release.write_text(json.dumps({**result["release"], "plan_digest": "wrong"}))
    assert not preflight(replace(settings, release_file=release))["ok"]


def test_release_refresh_updates_plan_and_pins_without_restart(tmp_path, monkeypatch):
    from server import deployed

    old = tmp_path / "releases" / "old"
    new = tmp_path / "releases" / "new"
    trust = new / "static/aiab/baked/trust.json"
    trust.parent.mkdir(parents=True)
    trust.write_text("{}")
    settings = replace(load_settings(), run_plan_dir=old / "plan", repo="owner/repo")
    pins = new / "static/deploy/tinfoil/audit/release.json"
    pins.parent.mkdir(parents=True)
    pins.write_text(json.dumps({"application_source_commit": "a" * 40, "host": "new.example"}))
    monkeypatch.setattr(deployed, "revision", lambda data, sha: new)
    calls = []

    def fetch(data, url, branch):
        calls.append((data, url, branch))
        return new

    monkeypatch.setattr(deployed, "snapshot", fetch)
    updated = deployed.refresh(settings)
    assert calls == [(tmp_path, "https://github.com/owner/repo.git", "production")]
    assert updated.deployed_plan_dir == new / "plan"
    assert updated.release_file == new / "static/deploy/tinfoil/audit/release.json"
    assert updated.run_trust_file == trust
    assert settings.deployed_plan_dir == old / "plan"  # in-flight runs keep their snapshot
