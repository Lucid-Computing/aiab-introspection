"""Fetch an isolated, immutable production snapshot without touching editable drafts."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from dataclasses import replace
from pathlib import Path

_lock = threading.RLock()


def validate_box_name(name):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", name):
        raise ValueError("Invalid box name")
    return name


def snapshot(data: Path, url: str, branch: str) -> Path:
    with _lock:
        return _snapshot(data, url, branch)


def _snapshot(data: Path, url: str, branch: str) -> Path:
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
    return revision(data, sha)


def revision(data: Path, sha: str) -> Path:
    """Materialize an already-fetched immutable source commit, never an editable draft."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Release must pin an application source commit")
    with _lock:
        return _revision(data, sha)


def _revision(data, sha):
    mirror = data / "release-source.git"
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


def refresh(settings, box_name="default"):
    """Refresh run inputs after a release without restarting the UI or editing drafts."""
    validate_box_name(box_name)
    if not settings.run_plan_dir or not settings.repo:
        if box_name != "default":
            raise ValueError("Named boxes require a managed release checkout")
        return settings
    data = settings.run_plan_dir.parent.parent.parent
    # Only the managed /data/releases/<commit>/plan layout is auto-refreshed.
    if settings.run_plan_dir.parent.parent.name != "releases":
        if box_name != "default":
            raise ValueError("Named boxes require a managed release checkout")
        return settings
    deployed = snapshot(
        data, "https://github.com/" + settings.repo + ".git", settings.deploy_base or "production"
    )
    catalog = deployed / "static/deploy/tinfoil/audit/boxes"
    release_file = (
        deployed / "static/deploy/tinfoil/audit/release.json"
        if box_name == "default"
        else catalog / (box_name + ".json")
    )
    if not release_file.is_file():
        raise ValueError("Box not found in the released catalog")
    release = json.loads(release_file.read_text())
    source = revision(data, release["application_source_commit"])
    return replace(
        settings,
        run_plan_dir=source / "plan",
        release_file=release_file,
        box_url="https://" + release["host"],
        box_name=box_name,
        box_catalog_dir=catalog,
    )


def choices(settings):
    """Catalog entries are release records, not a claim that a box is healthy."""
    return ["default"] + (
        sorted(
            p.stem
            for p in settings.box_catalog_dir.glob("*.json")
            if re.fullmatch(r"[a-z][a-z0-9-]{0,39}", p.stem) and p.stem != "default"
        )
        if settings.box_catalog_dir
        else []
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
