import asyncio
import dataclasses
import subprocess
import time

from server import deploystatus as ds
from server import publish
from server.config import load_settings


def test_key_summary_names_the_github_failure():
    info = {k: None for k in ("ui", "production", "box", "submitted", "pr", "check", "release")}
    label, tone, busy = ds._key_summary(info, False, "HTTP 401, token rejected for /x")
    assert "401" in label and "copy" in label
    assert tone == "bad" and not busy


def test_production_trust_cached_reads_the_last_answer(monkeypatch):
    settings = load_settings()
    monkeypatch.setattr(ds, "_answers", {})
    assert ds.production_trust_cached(settings) is None
    key = (
        f"/repos/{settings.repo}/contents/static/aiab/baked/trust.json",
        (("ref", settings.deploy_base),),
        True,
    )
    ds._answers[key] = (time.monotonic(), '{"lab_key": "k"}')
    assert ds.production_trust_cached(settings) == {"lab_key": "k"}


def _git(*args, cwd):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@x", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def test_pull_now_says_why_it_did_not_move(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "-q", "-b", "main", cwd=origin)
    _git("commit", "-q", "--allow-empty", "-m", "one", cwd=origin)
    copy = tmp_path / "copy"
    _git("clone", "-q", str(origin), str(copy), cwd=tmp_path)
    (copy / "note.txt").write_text("local\n")
    assert publish.pull_now(copy) is False
    assert "local change" in publish.last_pull["error"]
    status = publish.copy_status(copy)
    assert status["branch"] == "main" and status["dirty"] == 1

    (copy / "note.txt").unlink()
    _git("commit", "-q", "--allow-empty", "-m", "two", cwd=origin)
    assert publish.pull_now(copy) is True
    assert publish.last_pull["error"] is None
    assert publish.copy_status(copy)["behind"] == 0


def test_forget_keys_drops_only_the_key_related_answers(monkeypatch):
    monkeypatch.setattr(ds, "_answers", {})
    now = time.monotonic()
    ds._answers[("/repos/o/r/pulls", (), False)] = (now, [])
    ds._answers[("/repos/o/r/contents/static/aiab/baked/trust.json", (("ref", "p"),), True)] = (
        now,
        "{}",
    )
    ds._answers[("/repos/o/r/actions/runs", (("head_sha", "x"),), False)] = (now, {})
    ds._answers[("/repos/o/r/branches/p", (), False)] = (now, {})
    ds.forget_keys()
    assert list(ds._answers) == [("/repos/o/r/branches/p", (), False)]


def test_set_aside_stashes_local_changes_so_the_copy_can_catch_up(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "-q", "-b", "main", cwd=origin)
    (origin / "a.txt").write_text("one\n")
    _git("add", "a.txt", cwd=origin)
    _git("commit", "-q", "-m", "one", cwd=origin)
    copy = tmp_path / "copy"
    _git("clone", "-q", str(origin), str(copy), cwd=tmp_path)
    (copy / "a.txt").write_text("edited\n")
    (copy / "new.txt").write_text("untracked\n")
    assert publish.copy_status(copy)["changes"] == ["M a.txt", "?? new.txt"]
    note = publish.set_aside(copy)
    assert "set aside" in note or "Saved" in note
    assert publish.copy_status(copy)["dirty"] == 0
    assert (copy / "a.txt").read_text() == "one\n" and not (copy / "new.txt").exists()
    assert (
        "set aside by the agreement UI"
        in subprocess.run(["git", "stash", "list"], cwd=copy, capture_output=True, text=True).stdout
    )


def test_key_pull_request_into_another_branch_is_not_this_servers(monkeypatch):
    """Production must not read dev's merged key pull request as a key on its way."""

    def pr(number, base, branch):
        return {
            "number": number,
            "html_url": f"https://github.com/o/r/pull/{number}",
            "state": "closed",
            "merged_at": "2026-11-10T06:08:42Z",
            "base": {"ref": base},
            "head": {"ref": branch, "sha": f"sha{number}"},
            "merge_commit_sha": f"merge{number}",
        }

    prs = [pr(57, "dev", "keys/auditor-new"), pr(40, "production", "keys/auditor-old")]

    async def fake_get(client, path, params=None, raw=False):
        return prs if path.endswith("/pulls") else {"workflow_runs": []}

    monkeypatch.setattr(ds, "_get", fake_get)
    matches = lambda ref: ref.startswith("keys/auditor-")  # noqa: E731
    base = load_settings()

    production = dataclasses.replace(base, repo="o/r", deploy_base="production")
    found = asyncio.run(ds._pull_request(production, None, matches))
    assert found["pr"]["number"] == 40

    dev = dataclasses.replace(base, repo="o/r", deploy_base="dev")
    assert asyncio.run(ds._pull_request(dev, None, matches))["pr"]["number"] == 57

    elsewhere = dataclasses.replace(base, repo="o/r", deploy_base="master")
    assert asyncio.run(ds._pull_request(elsewhere, None, matches)) is None
