"""Where a deployment stands, read from GitHub: the pull request, its signature check, the
release that follows the merge. The box's own state comes from box.py.

Public repositories answer without a token, but only 60 times an hour per address, so the
answer is cached for a short while and a token is used when one is around (GITHUB_TOKEN on
the server, `gh auth token` on a laptop)."""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any

import httpx

from server.config import Settings

API = "https://api.github.com"
RELEASE_WORKFLOW = "Release to the live box"
TTL_SECONDS = 20
_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


def _token() -> str | None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    try:
        done = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
        return done.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


async def _get(client: httpx.AsyncClient, path: str, params: dict | None = None):
    response = await client.get(API + path, params=params)
    return response.json() if response.status_code == 200 else None


async def status(settings: Settings, digest: str, box_matches: bool) -> dict[str, Any] | None:
    """The deployment of one plan version, or None when no pull request exists for it."""
    if not digest or not settings.repo:
        return None
    key = (settings.repo, digest)
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    suffix = "-" + digest[7:15]  # Deploy names its branch plan/<name>-<digest8>
    headers = {"Accept": "application/vnd.github+json"}
    token = _token()
    if token:
        headers["Authorization"] = "Bearer " + token
    out: dict[str, Any] = {"pr": None, "check": None, "release": None, "error": None}
    try:
        async with httpx.AsyncClient(timeout=6.0, headers=headers) as client:
            prs = (
                await _get(
                    client,
                    f"/repos/{settings.repo}/pulls",
                    {"state": "all", "sort": "updated", "direction": "desc", "per_page": 30},
                )
                or []
            )
            pr = next(
                (
                    p
                    for p in prs
                    if p["head"]["ref"].startswith("plan/") and p["head"]["ref"].endswith(suffix)
                ),
                None,
            )
            if pr is None:
                _cache[key] = (time.monotonic() + TTL_SECONDS, out)
                return None
            merged_at = pr.get("merged_at")
            out["pr"] = {
                "number": pr["number"],
                "url": pr["html_url"],
                "state": "merged" if merged_at else pr["state"],
                "base": pr["base"]["ref"],
            }
            runs = (
                await _get(
                    client,
                    f"/repos/{settings.repo}/actions/runs",
                    {"head_sha": pr["head"]["sha"], "per_page": 5},
                )
                or {}
            )
            check = next(
                (r for r in runs.get("workflow_runs", []) if r["event"] == "pull_request_target"),
                None,
            )
            if check:
                out["check"] = _run(check)
            if merged_at:
                runs = (
                    await _get(
                        client,
                        f"/repos/{settings.repo}/actions/runs",
                        {"branch": pr["base"]["ref"], "event": "push", "per_page": 10},
                    )
                    or {}
                )
                release = next(
                    (
                        r
                        for r in runs.get("workflow_runs", [])
                        if r["name"] == RELEASE_WORKFLOW and r["created_at"] >= merged_at
                    ),
                    None,
                )
                if release:
                    out["release"] = _run(release)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        out["error"] = type(exc).__name__
    out["box_matches"] = box_matches
    out["busy"] = _busy(out)
    out["label"], out["tone"] = _summary(out)
    _cache[key] = (time.monotonic() + TTL_SECONDS, out)
    return out


def _run(run: dict) -> dict[str, Any]:
    return {"status": run["status"], "conclusion": run["conclusion"], "url": run["html_url"]}


def _busy(out: dict[str, Any]) -> bool:
    """True while something is still expected to change on its own."""
    pr, check, release = out["pr"], out["check"], out["release"]
    if pr is None or pr["state"] == "closed" or pr["base"] != "production":
        return False
    if pr["state"] == "open":
        return check is None or check["status"] != "completed" or check["conclusion"] == "success"
    if release is None or release["status"] != "completed":
        return True
    return release["conclusion"] == "success" and not out["box_matches"]


def _summary(out: dict[str, Any]) -> tuple[str, str]:
    """One short line for the agreement row: what the deployment is doing, and a tone
    (now = in progress, ok = done, bad = stopped)."""
    pr, check, release = out["pr"], out["check"], out["release"]
    if pr["base"] != "production":
        return "deploy failed · pull request against the wrong branch", "bad"
    if pr["state"] == "closed":
        return "deploy cancelled · pull request closed", "bad"
    if pr["state"] == "open":
        if check is None or check["status"] != "completed":
            return "deploying · checking the signatures", "now"
        if check["conclusion"] == "success":
            return "deploying · merging", "now"
        return "deploy failed · the signature check rejected it", "bad"
    if release is None or release["status"] != "completed":
        return "deploying · release to the box running", "now"
    if release["conclusion"] != "success":
        return "deploy failed · the release stopped", "bad"
    if out["box_matches"]:
        return "deployed · running in the box", "ok"
    return "deploying · waiting for the box to come back", "now"


NO_PR = {"label": "selected · no pull request found", "tone": "bad", "busy": False}
