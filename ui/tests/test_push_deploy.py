import subprocess
from pathlib import Path

from server import publish


def _git(*args, cwd):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@x", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write_plan(folder: Path, body: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plan.py").write_text(body)
    (folder / "plan.json").write_text('{"plan_id": "x"}\n')


def _setup(tmp_path):
    origin = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "production", str(origin), cwd=tmp_path)
    seed = tmp_path / "seed"
    _git("clone", "-q", str(origin), str(seed), cwd=tmp_path)
    _git("checkout", "-q", "-b", "production", cwd=seed)
    _write_plan(seed / "plan", "old = 1\n")
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "plan: old", cwd=seed)
    _git("push", "-q", "-u", "origin", "production", cwd=seed)
    return origin, seed


def test_redeploy_rebuilds_the_branch_from_the_current_base(tmp_path):
    origin, seed = _setup(tmp_path)
    # Yesterday's deploy of version "new" left its branch on origin, based on an older base.
    _git("checkout", "-q", "-b", "plan/x-abcdef12", cwd=seed)
    _write_plan(seed / "plan", "new = 1\n")
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "stale deploy", cwd=seed)
    _git("push", "-q", "-u", "origin", "plan/x-abcdef12", cwd=seed)
    stale = _git("rev-parse", "HEAD", cwd=seed)
    # Since then the base moved on (a key change, say).
    _git("checkout", "-q", "production", cwd=seed)
    (seed / "trust.txt").write_text("new key\n")
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "trust: new key", cwd=seed)
    _git("push", "-q", "origin", "production", cwd=seed)
    base_tip = _git("rev-parse", "HEAD", cwd=seed)

    agreement = tmp_path / "agreement"
    _write_plan(agreement, "new = 1\n")
    repo = tmp_path / "ui-copy"
    _git("clone", "-q", "-b", "production", str(origin), str(repo), cwd=tmp_path)
    result = publish.push_deploy(
        repo, agreement, "x", "sha256:abcdef1234567890", {"party": "lab"}, base="production"
    )
    assert result.error is None and not result.unchanged
    assert result.branch == "plan/x-abcdef12"
    _git("fetch", "-q", "origin", cwd=seed)
    tip = _git("rev-parse", "origin/plan/x-abcdef12", cwd=seed)
    assert tip != stale, "the stale branch must be replaced"
    assert _git("rev-parse", f"{tip}^", cwd=seed) == base_tip, "rebuilt from the current base"
    assert _git("diff", "--name-only", base_tip, tip, cwd=seed).splitlines() == ["plan/plan.py"]


def test_deploy_of_what_the_base_already_holds_is_unchanged(tmp_path):
    origin, _ = _setup(tmp_path)
    agreement = tmp_path / "agreement"
    _write_plan(agreement, "old = 1\n")
    repo = tmp_path / "ui-copy"
    _git("clone", "-q", "-b", "production", str(origin), str(repo), cwd=tmp_path)
    result = publish.push_deploy(
        repo, agreement, "x", "sha256:abcdef1234567890", {"party": "lab"}, base="production"
    )
    assert result.unchanged and result.error is None and result.commit is None
