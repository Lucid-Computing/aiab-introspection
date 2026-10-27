"""Fetch an isolated, immutable production snapshot without touching editable drafts."""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path


def snapshot(data: Path, url: str, branch: str) -> Path:
    mirror = data / "release-source.git"
    data.mkdir(parents=True, exist_ok=True)
    if not mirror.exists():
        subprocess.run(["git", "init", "--bare", str(mirror)], check=True, capture_output=True)
    subprocess.run(
        ["git", "--git-dir", str(mirror), "fetch", "--quiet", url, "refs/heads/" + branch],
        check=True,
        capture_output=True,
        timeout=120,
    )
    sha = subprocess.check_output(
        ["git", "--git-dir", str(mirror), "rev-parse", "FETCH_HEAD"],
        text=True,
    ).strip()
    target = data / "releases" / sha
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "--git-dir", str(mirror), "worktree", "add", "--detach", str(target), sha],
            check=True,
            capture_output=True,
            timeout=120,
        )
    return target


def refresh(settings):
    """Refresh run inputs after a release without restarting the UI or editing drafts."""
    if not settings.run_plan_dir or not settings.repo:
        return settings
    data = settings.run_plan_dir.parent.parent.parent
    # Only the managed /data/releases/<commit>/plan layout is auto-refreshed.
    if settings.run_plan_dir.parent.parent.name != "releases":
        return settings
    deployed = snapshot(
        data, "https://github.com/" + settings.repo + ".git", settings.deploy_base or "production"
    )
    return replace(
        settings,
        run_plan_dir=deployed / "plan",
        release_file=deployed / "static/deploy/tinfoil/audit/release.json",
    )


if __name__ == "__main__":
    print(
        snapshot(
            Path(os.environ.get("AIAB_DATA", "/data")),
            os.environ.get("AIAB_REPO_URL")
            or "https://github.com/" + os.environ["AIAB_REPO"] + ".git",
            os.environ.get("AIAB_RELEASE_BRANCH", "production"),
        )
    )
