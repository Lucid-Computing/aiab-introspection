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
