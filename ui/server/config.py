"""Environment-driven settings for the agreement UI and its backend integrations."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = UI_ROOT.parent
STATIC_DIR = REPO_ROOT / "static"
TEST_PROXY_URL = (
    "https://47b9b017832cbbf8ca2572b480584460df142e04-8080.dstack-pha-prod9.phala.network"
)


@dataclass(frozen=True)
class Settings:
    plan_dir: Path
    agreements_dir: Path
    git_dir: Path
    deploy_base: str | None
    trust_file: Path
    box_url: str | None
    accounts_file: Path
    session_secret: bytes
    repo: str | None
    runs_dir: Path  # one folder per audit run started from the Runs screen
    demo_keys_dir: Path  # lab.token and logger.key for the live box, never in the repo
    release_file: Path  # the release pins the live box must match
    push_drafts: bool  # push agreement folders to the checkout's branch after each save
    auto_pull: bool  # fast-forward the checkout to its remote branch every minute when clean
    logs_dir: Path  # downloaded proxy log segments, one folder
    proxy_url: str  # the logging proxy's address (its /_logs download API)
    proxy_key: str | None  # the "tester" key for that API; None means the Logs page is read-only

    run_plan_dir: Path | None = None
    anthropic_api_key: str | None = field(default=None, repr=False)
    # Signatures are checked against production's trust file as GitHub serves it, the same
    # file the plan check and the next release use; the copy on disk stands in only when
    # GitHub has not answered. Off when AIAB_TRUST_FILE names a dev trust file.
    trust_follows_production: bool = False

    @property
    def deployed_plan_dir(self) -> Path:
        return self.run_plan_dir or self.plan_dir

    @property
    def run_trust_file(self) -> Path:
        if self.run_plan_dir:
            candidate = self.run_plan_dir.parent / "static/aiab/baked/trust.json"
            if candidate.is_file():
                return candidate
        return self.trust_file

    @property
    def repo_url(self) -> str | None:
        return f"https://github.com/{self.repo}" if self.repo else None


def _repo_from_git() -> str | None:
    try:
        url = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    for prefix in ("git@github.com:", "https://github.com/"):
        if url.startswith(prefix):
            return url.removeprefix(prefix).removesuffix(".git")
    return None


def _load_dotenv(path: Path) -> None:
    """ui/.env, if present: KEY=VALUE lines, applied only where the environment is unset.

    Secrets for a laptop run live there, git-ignored; on Render the same names are set in
    the service's environment and this file does not exist.
    """
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def load_settings() -> Settings:
    _load_dotenv(UI_ROOT / ".env")
    env = os.environ
    secret = env.get("UI_SESSION_SECRET")
    release_file = Path(
        env.get("AIAB_RELEASE_FILE", STATIC_DIR / "deploy/tinfoil/audit/release.json")
    ).resolve()
    box_url = env.get("AIAB_BOX_URL", "").rstrip("/")
    if not box_url and release_file.is_file():
        box_url = "https://" + json.loads(release_file.read_text())["host"]
    repo = env.get("AIAB_REPO") or _repo_from_git()
    return Settings(
        run_plan_dir=Path(env["AIAB_RUN_PLAN_DIR"]).resolve()
        if env.get("AIAB_RUN_PLAN_DIR")
        else None,
        plan_dir=Path(env.get("AIAB_PLAN_DIR", REPO_ROOT / "plan")).resolve(),
        agreements_dir=Path(env.get("AIAB_AGREEMENTS_DIR", REPO_ROOT / "agreements")).resolve(),
        git_dir=Path(env.get("AIAB_GIT_DIR", REPO_ROOT)).resolve(),
        deploy_base=env.get("AIAB_DEPLOY_BASE") or "production",  # the branch the box is built from
        # The keys signatures are checked against: the checkout's trust.json, i.e. the one on
        # the deploy branch, the same file the plan check and the release use. Never edited
        # here; a key changes through a pull request. AIAB_TRUST_FILE overrides for dev keys.
        trust_file=Path(
            env.get("AIAB_TRUST_FILE")
            or Path(env.get("AIAB_GIT_DIR", REPO_ROOT)) / "static" / "aiab" / "baked" / "trust.json"
        ).resolve(),
        box_url=box_url or None,
        accounts_file=Path(env.get("UI_ACCOUNTS_FILE", UI_ROOT / "accounts.json")).resolve(),
        session_secret=secret.encode("utf-8") if secret else secrets.token_bytes(32),
        repo=repo,
        trust_follows_production=bool(repo) and not env.get("AIAB_TRUST_FILE"),
        runs_dir=Path(env.get("AIAB_RUNS_DIR", UI_ROOT / "runs")).resolve(),
        demo_keys_dir=Path(
            env.get("AIAB_DEMO_KEYS", Path.home() / ".local/share/aiab-audit-demo")
        ).resolve(),
        release_file=release_file,
        push_drafts=env.get("AIAB_PUSH_DRAFTS", "").lower() in {"1", "true", "yes"},
        auto_pull=env.get("AIAB_AUTO_PULL", "").lower() in {"1", "true", "yes"},
        logs_dir=Path(env.get("AIAB_LOGS_DIR", UI_ROOT / "logs")).resolve(),
        proxy_url=env.get("AIAB_PROXY_URL", TEST_PROXY_URL).rstrip("/"),
        proxy_key=_proxy_key(env.get("AIAB_PROXY_KEY", "")),
        anthropic_api_key=env.get("ANTHROPIC_API_KEY", "").strip() or None,
    )


def _proxy_key(value: str) -> str | None:
    """The key as pasted from 1Password: either bare, or a "name:key,name:key" list, in which
    case the "tester" entry (or the first) is used."""
    value = value.strip()
    if not value:
        return None
    if ":" not in value:
        return value
    pairs = dict(p.strip().split(":", 1) for p in value.split(",") if ":" in p)
    return pairs.get("tester") or next(iter(pairs.values()), None)
