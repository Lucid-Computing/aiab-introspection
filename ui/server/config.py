"""Environment-driven settings for the agreement UI. No secrets beyond the session key."""

from __future__ import annotations

import os
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = UI_ROOT.parent


@dataclass(frozen=True)
class Settings:
    plan_dir: Path
    box_url: str | None
    trusted_parties: Path | None
    accounts_file: Path
    session_secret: bytes
    repo: str | None
    dry_run_timeout: int

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


def load_settings() -> Settings:
    env = os.environ
    secret = env.get("UI_SESSION_SECRET")
    trusted = env.get("AIAB_TRUSTED_PARTIES")
    return Settings(
        plan_dir=Path(env.get("AIAB_PLAN_DIR", REPO_ROOT / "plan")).resolve(),
        box_url=(env.get("AIAB_BOX_URL") or "").rstrip("/") or None,
        trusted_parties=Path(trusted).resolve() if trusted else None,
        accounts_file=Path(env.get("UI_ACCOUNTS_FILE", UI_ROOT / "accounts.json")).resolve(),
        session_secret=secret.encode("utf-8") if secret else secrets.token_bytes(32),
        repo=env.get("AIAB_REPO") or _repo_from_git(),
        dry_run_timeout=int(env.get("UI_DRY_RUN_TIMEOUT", "120")),
    )
