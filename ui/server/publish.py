"""Deploy = copy the agreement into plan/, then push that one change as a branch and open a PR.

Everything here is plain git and the GitHub CLI, run with the credentials of whoever runs
the UI. The branch is made from the remote's default branch in a temporary worktree, so the
checkout you are working in is never switched. Merging is left to people and to CI.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PushResult:
    branch: str | None = None
    commit: str | None = None
    pr_url: str | None = None
    compare_url: str | None = None
    error: str | None = None
    unchanged: bool = False  # the base branch already has this exact agreement


def _git(repo: Path, *args: str, timeout: int = 120) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout
    )
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout).strip()[-400:] or f"git {args[0]} failed")
    return done.stdout.strip()


def default_branch(repo: Path) -> str:
    try:
        ref = _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD")
        return ref.rsplit("/", 1)[-1]
    except RuntimeError:
        return "master"


def github_slug(repo: Path) -> str | None:
    try:
        url = _git(repo, "remote", "get-url", "origin")
    except RuntimeError:
        return None
    for prefix in ("git@github.com:", "https://github.com/"):
        if url.startswith(prefix):
            return url.removeprefix(prefix).removesuffix(".git")
    return None


def copy_into_plan(source: Path, target: Path) -> None:
    """Make plan/ exactly the agreement folder: stale files go, README.md stays, and the
    change log (history.jsonl) stays with the agreement, it is not part of the baked plan."""
    if target.exists():
        for child in target.iterdir():
            if child.name == "README.md":
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "history.jsonl"),
        dirs_exist_ok=True,
    )


def _open_pr(
    slug: str, base: str, branch: str, name: str, digest: str, who: str, cwd: Path
) -> str | None:
    """Open the pull request with the GitHub CLI when it is installed and logged in."""
    if not shutil.which("gh"):
        return None
    body = (
        f"Plan digest `{digest}`.\n\nSelected for deployment by {who}. "
        "Plan-only change; CI verifies both signatures and the restrictions."
    )
    done = subprocess.run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            slug,
            "--base",
            base,
            "--head",
            branch,
            "--title",
            f"Deploy agreement {name}",
            "--body",
            body,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(cwd),
    )
    lines = done.stdout.strip().splitlines()
    if done.returncode == 0 and lines and lines[-1].startswith("http"):
        return lines[-1]
    return None


def push_deploy(
    repo: Path,
    agreement: Path,
    name: str,
    digest: str,
    actor: dict[str, str],
    base: str | None = None,
) -> PushResult:
    """Replace plan/ on the branch for this version, commit, push.

    One branch per version: plan/<name>-<digest8>. The first Deploy starts it from the
    base (origin's default branch); a later Deploy of the same version, for example after
    the second party signed, adds a commit to that same branch so the open pull request
    simply updates. If the branch already has these exact files, nothing is pushed.
    """
    result = PushResult()
    base = base or default_branch(repo)
    branch = f"plan/{name}-{digest[7:15]}"
    who = f"{actor.get('party', 'party')} ({actor.get('username', '?')}) via the agreement UI"
    worktree = Path(tempfile.mkdtemp(prefix="aiab-deploy-"))
    try:
        _git(repo, "fetch", "origin", base)
        on_remote = bool(_git(repo, "ls-remote", "--heads", "origin", branch))
        if on_remote:
            _git(repo, "fetch", "origin", branch)
        start = f"origin/{branch}" if on_remote else f"origin/{base}"
        _git(repo, "worktree", "add", "--detach", str(worktree), start)
        _git(worktree, "checkout", "-q", "-B", branch)
        copy_into_plan(agreement, worktree / "plan")
        _git(worktree, "add", "-A", "plan")
        slug = github_slug(repo)
        if slug:
            result.compare_url = f"https://github.com/{slug}/compare/{base}...{branch}?expand=1"
        if not _git(worktree, "status", "--porcelain", "--", "plan"):
            result.unchanged = True
            result.branch = branch
            return result
        message = (
            f"Deploy agreement {name}\n\nplan_digest: {digest}\nSelected for deployment by {who}.\n"
        )
        _git(worktree, "commit", "-q", "-m", message)
        result.commit = _git(worktree, "rev-parse", "--short", "HEAD")
        _git(worktree, "push", "-u", "origin", branch, timeout=300)
        result.branch = branch
        if slug and not on_remote:
            result.pr_url = _open_pr(slug, base, branch, name, digest, who, worktree)
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        result.error = str(exc)[-400:]
    finally:
        try:
            _git(repo, "worktree", "remove", "--force", str(worktree))
        except RuntimeError:
            shutil.rmtree(worktree, ignore_errors=True)
        try:
            _git(repo, "branch", "-D", branch)  # the branch lives on origin; keep the checkout tidy
        except RuntimeError:
            pass
    return result


# ---- agreement drafts: pushed to the checkout's own branch, so the server disk is never the
#      only copy. Drafts are not baked and CI does not check them, so a plain push is right.

log = logging.getLogger("aiab.drafts")
_drafts_lock = threading.Lock()
_drafts_timer: threading.Timer | None = None
DRAFTS_DELAY = 5.0  # seconds; several quick saves become one commit


def push_drafts(repo: Path, actor: dict[str, str] | None = None) -> str:
    """Commit agreements/ in the checkout and push it to the branch the checkout is on.

    Returns a short status line. Only the checkout's own branch is pushed, never another
    one, so a laptop checkout on a feature branch stays untouched unless asked.
    """
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        return "detached checkout; drafts not pushed"
    _git(repo, "add", "-A", "agreements")
    if not _git(repo, "status", "--porcelain", "--", "agreements"):
        return "no draft changes"
    who = f"{(actor or {}).get('party', 'party')} ({(actor or {}).get('username', '?')})"
    _git(
        repo, "commit", "-q", "-m", f"Save agreement drafts\n\nSaved by {who} via the agreement UI."
    )
    try:
        _git(repo, "push", "origin", f"HEAD:refs/heads/{branch}", timeout=120)
    except RuntimeError:
        # Someone else pushed first (another save, a merge from master). Replay on top.
        _git(repo, "pull", "--rebase", "--quiet", "origin", branch, timeout=120)
        _git(repo, "push", "origin", f"HEAD:refs/heads/{branch}", timeout=120)
    return f"drafts pushed to {branch}"


def schedule_drafts_push(repo: Path, actor: dict[str, str] | None = None) -> None:
    """Push a few seconds after the last save, in the background, one push for a burst."""
    global _drafts_timer

    def run() -> None:
        with _drafts_lock:
            try:
                log.info(push_drafts(repo, actor))
            except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
                log.warning("drafts push failed: %s", str(exc)[-300:])

    with _drafts_lock:
        if _drafts_timer is not None:
            _drafts_timer.cancel()
        _drafts_timer = threading.Timer(DRAFTS_DELAY, run)
        _drafts_timer.daemon = True
        _drafts_timer.start()
