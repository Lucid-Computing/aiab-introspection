"""Settings, all from the environment. Nothing here has a default that would let the
server run half configured: a missing key stops it at start."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    proxy_dir: Path  # the logging proxy's data directory, mounted read-only
    api_key: str = field(repr=False)  # what a caller of this server must send as x-log-key
    box_host: str  # the box records are sealed to: its Tinfoil host ...
    box_repo: str  # ... and the config repo its attestation is checked against
    sync_seconds: int  # how often the proxy's files are read again


def load(env: dict[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env

    def need(name: str) -> str:
        value = env.get(name, "").strip()
        if not value:
            raise ConfigError(f"{name} is not set")
        return value

    return Settings(
        proxy_dir=Path(env.get("PROXY_DIR", "").strip() or "/proxy-data"),
        api_key=need("API_KEY"),
        box_host=need("BOX_HOST"),
        box_repo=need("BOX_REPO"),
        sync_seconds=int(env.get("SYNC_SECONDS", "").strip() or 300),
    )
