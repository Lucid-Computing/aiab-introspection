"""Environment-driven settings for the agreement UI. No secrets beyond the session key."""

from __future__ import annotations

import os
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = UI_ROOT.parent
STATIC_DIR = REPO_ROOT / "static"


@dataclass(frozen=True)
class Settings:
    plan_dir: Path
    agreements_dir: Path
    trust_file: Path
    box_url: str | None
    accounts_file: Path
    session_secret: bytes
    repo: str | None

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
    return Settings(
        plan_dir=Path(env.get("AIAB_PLAN_DIR", REPO_ROOT / "plan")).resolve(),
        agreements_dir=Path(env.get("AIAB_AGREEMENTS_DIR", REPO_ROOT / "agreements")).resolve(),
        trust_file=Path(
            env.get("AIAB_TRUST_FILE", STATIC_DIR / "aiab" / "baked" / "trust.json")
        ).resolve(),
        box_url=(env.get("AIAB_BOX_URL") or "").rstrip("/") or None,
        accounts_file=Path(env.get("UI_ACCOUNTS_FILE", UI_ROOT / "accounts.json")).resolve(),
        session_secret=secret.encode("utf-8") if secret else secrets.token_bytes(32),
        repo=env.get("AIAB_REPO") or _repo_from_git(),
    )
