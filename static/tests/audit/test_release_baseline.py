"""Release baseline drift must be reviewed, including the organization migration."""

import json
import subprocess

import pytest

from deploy.tinfoil.audit.release import verify_config_baseline


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def baseline(tmp_path):
    repo, local = tmp_path / "repo", tmp_path / "approved"
    repo.mkdir()
    local.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    for name in ("tinfoil-config.yml", "trust.json", "manifest.json"):
        (repo / name).write_text("{}")
        (local / name).write_text("{}")
    for root in (repo, local):
        (root / "policy-digest.txt").write_text("sha256:test\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "baseline")
    git(repo, "tag", "v0.2.2")
    return repo, local, {"tag": "v0.2.2"}


def test_current_tag_is_default_baseline(baseline):
    verify_config_baseline(*baseline)


def test_reviewed_migration_requires_exact_commit_and_local_config(baseline):
    repo, local, prior = baseline
    (repo / "tinfoil-config.yml").write_text(json.dumps({"image": "new-org/same-digest"}))
    git(repo, "commit", "-qam", "migrate image namespace")
    with pytest.raises(RuntimeError, match="unreviewed"):
        verify_config_baseline(repo, local, prior)
    prior["reviewed_config_commit"] = git(repo, "rev-parse", "HEAD")
    with pytest.raises(RuntimeError, match="tinfoil-config"):
        verify_config_baseline(repo, local, prior)
    (local / "tinfoil-config.yml").write_bytes((repo / "tinfoil-config.yml").read_bytes())
    verify_config_baseline(repo, local, prior)
    git(repo, "commit", "--allow-empty", "-qm", "unexpected newer commit")
    with pytest.raises(RuntimeError, match="unreviewed"):
        verify_config_baseline(repo, local, prior)


@pytest.mark.parametrize("filename", ["trust.json", "manifest.json", "policy-digest.txt"])
def test_other_baseline_changes_rejected(baseline, filename):
    repo, local, prior = baseline
    (local / filename).write_text('{"changed":true}')
    with pytest.raises(RuntimeError, match="baseline"):
        verify_config_baseline(repo, local, prior)


def test_invalid_commit_rejected(baseline):
    repo, local, prior = baseline
    prior["reviewed_config_commit"] = "main"
    with pytest.raises(RuntimeError, match="Invalid reviewed"):
        verify_config_baseline(repo, local, prior)
