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
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("AIAB_PLAN_DIR", str(tmp_path / "unsigned"))
    monkeypatch.setenv("AIAB_RUN_PLAN_DIR", str(root / "plan"))
    monkeypatch.setenv("AIAB_RELEASE_FILE", str(root / "static/deploy/tinfoil/audit/release.json"))
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
