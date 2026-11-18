"""Settings, all from the environment. Nothing here has a default that would let the
server run half configured: a missing key stops it at start."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(RuntimeError):
    pass


NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class Target:
    """One box records can be sealed to, and the key of the one caller that may ask for it."""

    name: str  # e.g. "dev"; its settings are DEV_API_KEY, DEV_BOX_HOST, DEV_BOX_REPO
    api_key: str = field(repr=False)  # what that caller must send as x-log-key
    box_host: str  # the box's Tinfoil host ...
    box_repo: str  # ... and the config repo its attestation is checked against


@dataclass(frozen=True)
class Settings:
    proxy_dir: Path  # the logging proxy's data directory, mounted read-only
    boxes: tuple[Target, ...]  # at least one; no two share a key
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
        boxes=_boxes(env, need),
        sync_seconds=int(env.get("SYNC_SECONDS", "").strip() or 300),
    )


def _boxes(env, need) -> tuple[Target, ...]:
    """BOXES=dev,prod reads <NAME>_API_KEY, <NAME>_BOX_HOST and <NAME>_BOX_REPO for each
    name. Without BOXES there is one box, from API_KEY, BOX_HOST and BOX_REPO."""
    listed = [n.strip().lower() for n in env.get("BOXES", "").split(",") if n.strip()]
    if not listed:
        return (Target("default", need("API_KEY"), need("BOX_HOST"), need("BOX_REPO")),)
    if env.get("API_KEY", "").strip():
        raise ConfigError("set BOXES or API_KEY, not both")
    boxes = []
    for name in listed:
        if not NAME_RE.match(name):
            raise ConfigError(f"BOXES: {name!r} is not a name (letters, digits, _)")
        prefix = name.upper()
        boxes.append(
            Target(
                name,
                need(f"{prefix}_API_KEY"),
                need(f"{prefix}_BOX_HOST"),
                need(f"{prefix}_BOX_REPO"),
            )
        )
    if len({b.name for b in boxes}) != len(boxes):
        raise ConfigError("BOXES names a box twice")
    # A key decides the box, so two boxes with one key would make the choice a guess.
    if len({b.api_key for b in boxes}) != len(boxes):
        raise ConfigError("two boxes share an API key; each needs its own")
    return tuple(boxes)
