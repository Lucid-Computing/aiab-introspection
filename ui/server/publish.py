"""Deploy = copy the agreement into plan/, then push that one change as a branch and open a PR.

Everything here is plain git and the GitHub CLI, run with the credentials of whoever runs
the UI. The branch is made from the remote's default branch in a temporary worktree, so the
checkout you are working in is never switched. Merging is left to people and to CI.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import threading
import time
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


log = logging.getLogger("aiab.publish")


def _git(repo: Path, *args: str, timeout: int = 120) -> str:
    started = time.monotonic()
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout
    )
    took = time.monotonic() - started
    if took > 5 or done.returncode:  # a slow or failing step is what a server log should show
        log.warning("git %s: %.1fs, exit %d", " ".join(args[:2]), took, done.returncode)
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


def _open_pr(slug: str, base: str, branch: str, title: str, body: str, cwd: Path) -> str | None:
    """Open a pull request with the GitHub CLI when it is installed and logged in."""
    if not shutil.which("gh"):
        return None
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
            title,
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
            body = (
                f"Plan digest `{digest}`.\n\nSelected for deployment by {who}. "
                "Plan-only change; CI verifies both signatures and the restrictions."
            )
            result.pr_url = _open_pr(slug, base, branch, f"Deploy agreement {name}", body, worktree)
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


# ---- a party's public key: a pull request against the deploy base that changes only
#      static/aiab/baked/trust.json. A person merges it (approve-keys.yml checks, never merges).

TRUST_PATH = Path("static") / "aiab" / "baked" / "trust.json"


def push_key_change(
    repo: Path, party: str, public_key: str, actor: dict[str, str], base: str | None = None
) -> PushResult:
    """Open (or refresh) the pull request that makes production trust a party's key.

    One branch per key: keys/<party>-<key8>, always rebuilt from the current base, so a
    branch left behind by an earlier, closed pull request is simply overwritten and a new
    pull request opened. The check on it merges it; the release puts the key in the box."""
    result = PushResult()
    base = base or default_branch(repo)
    branch = f"keys/{party}-{public_key[:8].replace('+', '').replace('/', '')}"
    who = f"{actor.get('party', 'party')} ({actor.get('username', '?')}) via the agreement UI"
    worktree = Path(tempfile.mkdtemp(prefix="aiab-key-"))
    try:
        _git(repo, "fetch", "origin", base)
        _git(repo, "worktree", "add", "--detach", str(worktree), f"origin/{base}")
        _git(worktree, "checkout", "-q", "-B", branch)
        path = worktree / TRUST_PATH
        trust = json.loads(path.read_text(encoding="utf-8"))
        if trust.get(f"{party}_key") == public_key:
            result.unchanged, result.branch = True, base
            return result
        trust[f"{party}_key"] = public_key
        path.write_text(json.dumps(trust, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _git(worktree, "add", str(TRUST_PATH))
        _git(
            worktree,
            "commit",
            "-q",
            "-m",
            f"Trust the {party} public key\n\n{party}_key = {public_key}\nSubmitted by {who}.\n",
        )
        result.commit = _git(worktree, "rev-parse", "--short", "HEAD")
        _git(worktree, "push", "--force", "-u", "origin", branch, timeout=120)
        result.branch = branch
        slug = github_slug(repo)
        if slug:
            result.compare_url = f"https://github.com/{slug}/compare/{base}...{branch}?expand=1"
            result.pr_url = _open_pull_request(slug, branch)
            if result.pr_url:
                result.unchanged = True  # an open pull request already follows this branch
            else:
                body = (
                    f"Replaces the {party} public key in `static/aiab/baked/trust.json` with "
                    f"`{public_key}`.\n\nSubmitted by {who}. approve-keys.yml checks the change "
                    "and merges it; the release that follows puts the key into the box."
                )
                result.pr_url = _open_pr(
                    slug, base, branch, f"Trust the {party} public key", body, worktree
                )
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        result.error = str(exc)[-400:]
    finally:
        try:
            _git(repo, "worktree", "remove", "--force", str(worktree))
        except RuntimeError:
            shutil.rmtree(worktree, ignore_errors=True)
        try:
            _git(repo, "branch", "-D", branch)
        except RuntimeError:
            pass
    return result


def _open_pull_request(slug: str, branch: str) -> str | None:
    """The URL of the open pull request for this branch, if the GitHub CLI can tell."""
    if not shutil.which("gh"):
        return None
    done = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            slug,
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "url",
            "--jq",
            ".[0].url",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return done.stdout.strip() or None if done.returncode == 0 else None


# ---- the checkout follows its remote branch: after a plan merges and the release writes new
#      pins, the deployed plan shown on the Agreements page is current within a minute.


def pull_now(repo: Path) -> bool:
    """Fast-forward the checkout to its remote branch once, only when nothing is modified
    locally (agreement drafts are git-ignored, so normally nothing is). True when it moved."""
    try:
        if _git(repo, "status", "--porcelain"):
            return False
        before = _git(repo, "rev-parse", "HEAD")
        branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        _git(repo, "fetch", "--quiet", "origin", branch)
        _git(repo, "merge", "--quiet", "--ff-only", "FETCH_HEAD")
        return _git(repo, "rev-parse", "HEAD") != before
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        log.warning("pull: %s", str(exc)[-200:])
        return False


def start_auto_pull(repo: Path, every: int = 60) -> None:
    """Fast-forward the checkout to its remote branch every ``every`` seconds."""

    def loop() -> None:
        while True:
            pull_now(repo)
            time.sleep(every)

    threading.Thread(target=loop, daemon=True, name="auto-pull").start()
