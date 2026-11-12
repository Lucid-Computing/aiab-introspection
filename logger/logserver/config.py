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
    proxy_url: str  # the logging proxy's address; its /_logs download API is read from here
    proxy_header: str  # the header the proxy takes its reader key in
    proxy_key: str = field(repr=False)
    api_key: str = field(repr=False)  # what a caller of this server must send as x-log-key
    box_host: str  # the box records are sealed to: its Tinfoil host ...
    box_repo: str  # ... and the config repo its attestation is checked against
    data_dir: Path  # the proxy's segments, kept between syncs
    sync_seconds: int


def load(env: dict[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env

    def need(name: str) -> str:
        value = env.get(name, "").strip()
        if not value:
            raise ConfigError(f"{name} is not set")
        return value

    return Settings(
        proxy_url=need("PROXY_URL").rstrip("/"),
        proxy_header=env.get("PROXY_KEY_HEADER", "").strip() or "x-log-key",
        proxy_key=need("PROXY_KEY"),
        api_key=need("API_KEY"),
        box_host=need("BOX_HOST"),
        box_repo=need("BOX_REPO"),
        data_dir=Path(env.get("DATA_DIR", "").strip() or "/data"),
        sync_seconds=int(env.get("SYNC_SECONDS", "").strip() or 300),
    )
