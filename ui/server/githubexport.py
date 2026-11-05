"""The GitHub export: fetched from GitHub by hand, committed as an encrypted bundle.

GitHub Logs covers the Lucid-Computing organisation's public repositories, forks left
out: issues and pull requests with their comments, reviews and review comments, and the
commits on each default branch (message and changed files, not the diffs). ``fetch``
writes one JSON lines file per repository (the row format is in ``server.githublogs``).
Private repositories are never read: the organisation is asked for its public ones and
each is checked again before anything of it is fetched.

The bundle works like the Slack one (``server.slackexport``, same layout under its own
magic ``AIABGHB1``): ``ui/data/github-export.enc`` is sealed with AES-256-GCM under a key
that lives only in the servers' environment (AIAB_GITHUB_EXPORT_KEY) and in 1Password, and
the server opens it into AIAB_LOGS_DIR/github at start. A new fetch is a new bundle::

    cd ui && uv run python -m server.githubexport newkey > ~/.aiab-github-export.key
    export AIAB_GITHUB_EXPORT_KEY=$(cat ~/.aiab-github-export.key)
    uv run python -m server.githubexport fetch ~/github-export   # needs gh, signed in
    uv run python -m server.githubexport pack ~/github-export

``fetch`` calls the GitHub API through the ``gh`` command line, so it uses whoever is
signed in there; a few hundred calls for the whole organisation.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from server.slackexport import BundleError, new_key, pack, unpack

ORG = "Lucid-Computing"
MAGIC = b"AIABGHB1"
KEY_ENV = "AIAB_GITHUB_EXPORT_KEY"
WHAT = "GitHub export"


class FetchError(Exception):
    """GitHub did not answer as expected; the message says what was asked."""


def _gh(path: str) -> list | dict:
    """One GitHub API path through ``gh api``, every page of a list joined into one."""
    cmd = ["gh", "api", "-H", "Accept: application/vnd.github+json", path]
    paged = "?per_page=" in path or "&per_page=" in path
    if paged:
        cmd[2:2] = ["--paginate", "--slurp"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120).stdout
    except subprocess.CalledProcessError as exc:
        raise FetchError(f"gh api {path}: {exc.stderr.strip()}") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise FetchError(f"gh api {path}: {exc}") from exc
    data = json.loads(out)
    if paged:
        return [item for page in data for item in page]
    return data


def public_repos(org: str = ORG) -> list[dict]:
    """The organisation's public repositories that are not forks, by name."""
    repos = _gh(f"orgs/{org}/repos?type=public&per_page=100")
    keep = [
        r
        for r in repos
        if not r.get("private") and r.get("visibility") == "public" and not r.get("fork")
    ]
    return sorted(keep, key=lambda r: r["name"])


def _login(user: dict | None) -> str:
    return (user or {}).get("login") or "ghost"


def repo_rows(repo: dict) -> list[dict]:
    """Every post of one public repository, as rows for its JSON lines file."""
    name, full = repo["name"], repo["full_name"]
    check = _gh(f"repos/{full}")
    if check.get("private") or check.get("visibility") != "public":
        raise FetchError(f"{full} is not public; refusing to fetch it")
    rows: list[dict] = []
    titles: dict[int, str] = {}
    pulls: list[int] = []
    for t in _gh(f"repos/{full}/issues?state=all&per_page=100"):
        is_pull = "pull_request" in t
        number = t["number"]
        titles[number] = t.get("title") or ""
        meta = [f"state: {t.get('state')}"]
        if is_pull:
            pulls.append(number)
            if (t.get("pull_request") or {}).get("merged_at"):
                meta = ["state: merged"]
        labels = [lb.get("name") for lb in t.get("labels") or [] if lb.get("name")]
        if labels:
            meta.append("labels: " + ", ".join(labels))
        rows.append(
            {
                "repo": name,
                "kind": "pull" if is_pull else "issue",
                "id": t["id"],
                "number": number,
                "title": titles[number],
                "time": t["created_at"],
                "author": _login(t.get("user")),
                "url": t["html_url"],
                "text": t.get("body") or "",
                "meta": meta,
            }
        )
    for c in _gh(f"repos/{full}/issues/comments?per_page=100"):
        number = int(c["issue_url"].rsplit("/", 1)[1])
        rows.append(
            {
                "repo": name,
                "kind": "comment",
                "id": c["id"],
                "number": number,
                "title": titles.get(number, ""),
                "time": c["created_at"],
                "author": _login(c.get("user")),
                "url": c["html_url"],
                "text": c.get("body") or "",
                "meta": [],
            }
        )
    for number in pulls:
        for rv in _gh(f"repos/{full}/pulls/{number}/reviews?per_page=100"):
            if not rv.get("submitted_at"):
                continue  # a pending review: not posted
            rows.append(
                {
                    "repo": name,
                    "kind": "review",
                    "id": rv["id"],
                    "number": number,
                    "title": titles.get(number, ""),
                    "time": rv["submitted_at"],
                    "author": _login(rv.get("user")),
                    "url": rv.get("html_url") or "",
                    "text": rv.get("body") or "",
                    "meta": [f"review: {(rv.get('state') or '').lower().replace('_', ' ')}"],
                }
            )
    for c in _gh(f"repos/{full}/pulls/comments?per_page=100"):
        number = int(c["pull_request_url"].rsplit("/", 1)[1])
        where = c.get("path") or ""
        if c.get("line") or c.get("original_line"):
            where += f":{c.get('line') or c.get('original_line')}"
        rows.append(
            {
                "repo": name,
                "kind": "review_comment",
                "id": c["id"],
                "number": number,
                "title": titles.get(number, ""),
                "time": c["created_at"],
                "author": _login(c.get("user")),
                "url": c["html_url"],
                "text": c.get("body") or "",
                "meta": [f"on {where}"] if where else [],
            }
        )
    try:
        commits = _gh(f"repos/{full}/commits?per_page=100")
    except FetchError as exc:
        if "empty" in str(exc).lower():  # a repository with no commits yet
            commits = []
        else:
            raise
    for c in commits:
        detail = _gh(f"repos/{full}/commits/{c['sha']}")
        files = detail.get("files") or []
        stats = detail.get("stats") or {}
        meta = [
            f"{len(files)} file{'s' if len(files) != 1 else ''} changed, "
            f"+{stats.get('additions', 0)} −{stats.get('deletions', 0)}"
        ]
        if files:
            shown = [f["filename"] for f in files[:20]]
            more = f" and {len(files) - 20} more" if len(files) > 20 else ""
            meta.append("files: " + ", ".join(shown) + more)
        author = c.get("author") or {}
        rows.append(
            {
                "repo": name,
                "kind": "commit",
                "id": c["sha"],
                "sha": c["sha"],
                "time": c["commit"]["author"]["date"],
                "author": author.get("login") or c["commit"]["author"].get("name") or "?",
                "url": c["html_url"],
                "text": c["commit"].get("message") or "",
                "meta": meta,
            }
        )
    rows.sort(key=lambda r: r["time"])
    return rows


def fetch(folder: Path, org: str = ORG, log=print) -> list[str]:
    """Fetch every public repository of ``org`` into ``folder``, one ``<repo>.jsonl`` each
    (files of repositories not fetched this time are removed); returns the file names."""
    folder.mkdir(parents=True, exist_ok=True)
    names = []
    for repo in public_repos(org):
        rows = repo_rows(repo)
        path = folder / f"{repo['name']}.jsonl"
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        names.append(path.name)
        log(f"{repo['name']}: {len(rows)} posts")
    for stale in folder.glob("*.jsonl"):
        if stale.name not in names:
            stale.unlink()
    return names


def unpack_at_start(settings) -> list[str] | None:
    """Open the committed bundle into the GitHub source's folder when the server holds the
    key and reads no folder of its own; None when there is nothing to do. Errors are
    reported, not raised: the server starts and GitHub Logs shows empty."""
    source = settings.log_source("github")
    if source.root is not None or not source.key:
        return None
    try:
        return unpack(
            settings.github_bundle, source.key, source.path(settings), MAGIC, KEY_ENV, WHAT
        )
    except BundleError as exc:
        print(f"warning: GitHub Logs stays empty: {exc}")
        return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="fetch, pack or open the GitHub export bundle")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("newkey", help=f"print a fresh key for {KEY_ENV}")
    f = sub.add_parser("fetch", help="fetch the organisation's public repositories")
    f.add_argument("folder", type=Path)
    f.add_argument("--org", default=ORG)
    p = sub.add_parser("pack", help="seal a folder's *.jsonl files into the bundle")
    p.add_argument("folder", type=Path)
    p.add_argument("--out", type=Path, default=None, help="default: the committed bundle path")
    u = sub.add_parser("unpack", help="open a bundle into a folder")
    u.add_argument("dest", type=Path)
    u.add_argument("--bundle", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.cmd == "newkey":
        print(new_key())
        return 0
    if args.cmd == "fetch":
        try:
            names = fetch(args.folder, args.org)
        except FetchError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"fetched {len(names)} repositories into {args.folder}")
        return 0
    from server.config import load_settings

    settings = load_settings()
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        print(f"set {KEY_ENV} (make one with: newkey)", file=sys.stderr)
        return 2
    try:
        if args.cmd == "pack":
            out = args.out or settings.github_bundle
            names = pack(args.folder, key, out, MAGIC, KEY_ENV)
            print(f"packed {len(names)} repository file(s) into {out} ({out.stat().st_size} bytes)")
        else:
            bundle = args.bundle or settings.github_bundle
            names = unpack(bundle, key, args.dest, MAGIC, KEY_ENV, WHAT)
            print(f"opened {len(names)} repository file(s) into {args.dest}")
    except BundleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
