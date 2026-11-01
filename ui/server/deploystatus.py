"""Where the parties' keys stand on GitHub and in the box, so the UI never claims more than
it knows: per party, the key this UI holds, the key production trusts, the key the box
trusts, and the pull request in between (``keys``). Answered from GitHub's API plus the
box's manifest.

Public repositories answer without a token, but only 60 times an hour per address, so
answers are cached for a short while and a token is used when one is around
(GITHUB_TOKEN on the server, `gh auth token` on a laptop)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from typing import Any

import httpx

from server.config import Settings

API = "https://api.github.com"
RELEASE_WORKFLOW = "Release to the live box"
TTL_SECONDS = 20
_cache: dict[tuple, tuple[float, Any]] = {}

# Every GitHub answer is kept and served at once; when it is older than TTL_SECONDS it is
# refreshed behind the page, and a background loop refreshes stale answers on its own, so
# a page load never waits for GitHub after the first time.
_answers: dict[tuple, tuple[float, Any]] = {}
_refreshing: set[tuple] = set()
# The last refusal or failure and the last success, so a page can say why GitHub did not
# answer ("HTTP 401, token rejected") instead of only that it did not.
_last_failure: dict[str, Any] | None = None
_last_success_at: float | None = None


def _token() -> str | None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    try:
        done = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
        return done.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _client() -> httpx.AsyncClient:
    headers = {"Accept": "application/vnd.github+json"}
    token = _token()
    if token:
        headers["Authorization"] = "Bearer " + token
    return httpx.AsyncClient(timeout=6.0, headers=headers)


async def _fetch(path: str, params: dict | None, raw: bool):
    global _last_failure, _last_success_at
    headers = {"Accept": "application/vnd.github.raw+json"} if raw else None
    async with _client() as client:
        response = await client.get(API + path, params=params, headers=headers)
    if response.status_code != 200:
        _last_failure = {"path": path, "status": response.status_code, "at": time.time()}
        return None
    _last_success_at = time.time()
    return response.text if raw else response.json()


async def _refresh(key: tuple, path: str, params: dict | None, raw: bool) -> None:
    if key in _refreshing:
        return
    _refreshing.add(key)
    try:
        _answers[key] = (time.monotonic(), await _fetch(path, params, raw))
    except (httpx.HTTPError, ValueError) as exc:
        global _last_failure
        _last_failure = {
            "path": path,
            "status": None,
            "error": type(exc).__name__,
            "at": time.time(),
        }
    finally:
        _refreshing.discard(key)


def github_state() -> dict[str, Any]:
    """Whether GitHub answered more recently than it failed, and the last failure."""
    ok = _last_success_at is not None and (
        _last_failure is None or _last_failure["at"] <= _last_success_at
    )
    return {"ok": ok, "last_success_at": _last_success_at, "last_failure": _last_failure}


def github_why() -> str:
    """The last failure in a few words, for a status line."""
    failure = _last_failure
    if failure is None:
        return "no answer yet"
    status = failure.get("status")
    if status:
        hint = {
            401: "token rejected",
            403: "forbidden or rate limited",
            404: "not found: repository, branch or token access",
        }.get(status, "")
        return f"HTTP {status}" + (f", {hint}" if hint else "") + f" for {failure['path']}"
    return f"{failure.get('error')} for {failure['path']}"


async def _get(client, path: str, params: dict | None = None, raw=False):
    """The cached answer when there is one (refreshing it behind the page if it is old),
    otherwise the live answer. ``client`` is unused and kept for the call sites."""
    key = (path, tuple(sorted((params or {}).items())), raw)
    hit = _answers.get(key)
    if hit is None:
        await _refresh(key, path, params, raw)
        hit = _answers.get(key)
        return hit[1] if hit else None
    if time.monotonic() - hit[0] > TTL_SECONDS:
        asyncio.ensure_future(_refresh(key, path, params, raw))
    return hit[1]


async def warm_up(settings: Settings) -> None:
    """Fetch what every Agreements page needs, so the first load is fast too."""
    if not settings.repo:
        return
    try:
        await production_trust(settings)
        await _get(
            None,
            f"/repos/{settings.repo}/pulls",
            {"state": "all", "sort": "updated", "direction": "desc", "per_page": 40},
        )
    except (httpx.HTTPError, ValueError):
        pass


async def refresh_loop(every: float = 15.0) -> None:
    """Keep every answer a page has ever asked for fresh, so pages load from memory."""
    while True:
        await asyncio.sleep(every)
        now = time.monotonic()
        for key, (at, _) in list(_answers.items()):
            if now - at > TTL_SECONDS:
                path, params, raw = key
                await _refresh(key, path, dict(params), raw)


def freshness() -> float | None:
    """Seconds since the oldest answer on the page was fetched, or None before the first."""
    if not _answers:
        return None
    return time.monotonic() - min(at for at, _ in _answers.values())


def _run(run: dict) -> dict[str, Any]:
    return {
        "status": run["status"],
        "conclusion": run["conclusion"],
        "url": run["html_url"],
        "finished": run.get("updated_at"),
    }


async def _pull_request(settings: Settings, client: httpx.AsyncClient, matches) -> dict | None:
    """The newest pull request whose head branch satisfies ``matches``, with its check run
    (the pull_request or pull_request_target workflow on its head) and, once merged, the
    release run that followed on the base branch."""
    prs = (
        await _get(
            client,
            f"/repos/{settings.repo}/pulls",
            {"state": "all", "sort": "updated", "direction": "desc", "per_page": 40},
        )
        or []
    )
    pr = next((p for p in prs if matches(p["head"]["ref"])), None)
    if pr is None:
        return None
    merged_at = pr.get("merged_at")
    out: dict[str, Any] = {
        "pr": {
            "number": pr["number"],
            "url": pr["html_url"],
            "state": "merged" if merged_at else pr["state"],
            "base": pr["base"]["ref"],
            "branch": pr["head"]["ref"],
        },
        "check": None,
        "release": None,
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
        (
            r
            for r in runs.get("workflow_runs", [])
            if r["event"] in ("pull_request", "pull_request_target")
        ),
        None,
    )
    if check:
        out["check"] = _run(check)
    if merged_at:
        runs = (
            await _get(
                client,
                f"/repos/{settings.repo}/actions/runs",
                {"head_sha": pr["merge_commit_sha"], "per_page": 30},
            )
            or {}
        )
        release = next(
            (r for r in runs.get("workflow_runs", []) if r["name"] == RELEASE_WORKFLOW),
            None,
        )
        if release:
            out["release"] = _run(release)
    return out


# ---- the parties' keys --------------------------------------------------------------------


async def production_trust(settings: Settings) -> dict[str, Any] | None:
    """trust.json as it is on the deploy base branch: the keys this UI verifies signatures
    against, and the keys the next release puts into the box."""
    text = None
    try:
        async with _client() as client:
            text = await _get(
                client,
                f"/repos/{settings.repo}/contents/static/aiab/baked/trust.json",
                {"ref": settings.deploy_base},
                raw=True,
            )
    except httpx.HTTPError:
        pass
    try:
        import json

        return json.loads(text) if text else None
    except ValueError:
        return None


def forget_keys() -> None:
    """Drop the cached answers the key status depends on, so the next look is live. Used
    while a key submission is in flight and the page polls for its progress."""
    for key in list(_answers):
        path = key[0]
        if path.endswith("/pulls") or path.endswith("/trust.json") or "/actions/runs" in path:
            _answers.pop(key, None)


def production_trust_cached(settings: Settings) -> dict[str, Any] | None:
    """Production's trust file from the last GitHub answer, without waiting for one."""
    key = (
        f"/repos/{settings.repo}/contents/static/aiab/baked/trust.json",
        (("ref", settings.deploy_base),),
        True,
    )
    hit = _answers.get(key)
    if not hit or not hit[1]:
        return None
    try:
        return json.loads(hit[1])
    except ValueError:
        return None


async def keys(
    settings: Settings, copy_keys: dict[str, str | None], box_keys: dict[str, str] | None
) -> dict[str, Any]:
    """Per party: the key production trusts (what this UI checks signatures against), the
    key in this server's copy of the repository, the key the box trusts, the pull request
    in between, and a one-line verdict. ``ready`` says whether every party's key is trusted
    on production, i.e. a plan signed with these keys is one the box accepts.
    When GitHub does not answer, the copy's keys stand in and the verdict says so."""
    trust = await production_trust(settings)
    out: dict[str, Any] = {
        "parties": {},
        "ready": False,
        "busy": False,
        "github": trust is not None,
        "github_why": None if trust is not None else github_why(),
    }
    for party, copy_key in copy_keys.items():
        prod_key = (trust or {}).get(f"{party}_key")
        box_key = (box_keys or {}).get(f"{party}_key")
        info: dict[str, Any] = {
            "ui": prod_key if trust is not None else copy_key,
            "copy": copy_key,
            "production": prod_key,
            "box": box_key,
            "pr": None,
            "check": None,
            "release": None,
        }
        info["submitted"] = None
        if trust is not None:
            try:
                async with _client() as client:
                    found = await _pull_request(
                        settings, client, lambda ref, p=party: ref.startswith(f"keys/{p}-")
                    )
                    if found:
                        info.update(found)
                        text = await _get(
                            client,
                            f"/repos/{settings.repo}/contents/static/aiab/baked/trust.json",
                            {"ref": found["pr"]["branch"]},
                            raw=True,
                        )
                        if text:
                            import json

                            info["submitted"] = json.loads(text).get(f"{party}_key")
            except (httpx.HTTPError, ValueError, KeyError):
                pass
        info["label"], info["tone"], info["busy"] = _key_summary(
            info, trust is not None, out["github_why"]
        )
        out["parties"][party] = info
    # When GitHub does not answer, the copy's keys stand in: that must not turn every
    # Run button grey, the verdict line says what happened instead.
    out["ready"] = all(p["ui"] for p in out["parties"].values())
    out["busy"] = any(p["busy"] for p in out["parties"].values())
    return out


def _key_summary(
    info: dict[str, Any], github_ok: bool, why: str | None = None
) -> tuple[str, str, bool]:
    """One line per party. ``production`` is the key on GitHub, which this UI checks
    against; ``box`` the box's; ``submitted`` the key in the party's latest pull request."""
    prod, box, sub = info["production"], info["box"], info["submitted"]
    pr, check, release = info["pr"], info["check"], info["release"]
    if not github_ok:
        return (
            f"could not read the trusted keys from GitHub ({why or 'no answer'}) · checking"
            " against this server's copy meanwhile",
            "bad",
            False,
        )
    if not prod:
        return "no key trusted on production", "bad", False
    if pr is not None and sub and sub != prod:
        short, n = sub[:8] + "…", pr["number"]
        if pr["state"] == "closed":
            return f"submission {short} · pull request #{n} was closed", "bad", False
        if pr["state"] == "open":
            if check is None or check["status"] != "completed":
                return f"submission {short} · pull request #{n} · checking", "now", True
            if check["conclusion"] == "success":
                return f"submission {short} · pull request #{n} · merging", "now", True
            return f"submission {short} · pull request #{n} · rejected by the check", "bad", False
        return f"submission {short} merged · GitHub still shows the old key", "now", True
    if box is None:
        return "trusted on production", "ok", False
    if box == prod:
        return "trusted on production and by the box", "ok", False
    if release and release["status"] != "completed":
        return "trusted on production · release running, the box takes it soon", "now", True
    return "trusted on production · the box takes it with the next release", "now", True
