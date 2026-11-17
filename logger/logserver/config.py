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
    # Which sources this caller sees and may export; all of them unless <NAME>_SOURCES says.
    sources: tuple[str, ...] = ("proxy", "plant")


@dataclass(frozen=True)
class Settings:
    proxy_dir: Path  # the logging proxy's data directory, mounted read-only
    boxes: tuple[Target, ...]  # at least one; no two share a key
    sync_seconds: int  # how often the proxy's files, or the hub, are read again
    sources: tuple[str, ...] = ("proxy",)  # what this server reads: "proxy", "plant" or both
    # The RSI plant's hub, a source when both are set (logserver.plant): its read API's
    # address, and a reader's token for it.
    hub_url: str = ""
    hub_key: str = field(default="", repr=False)
    data_dir: Path = Path("/data")  # where the hub's files are kept between starts


def load(env: dict[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env

    def need(name: str) -> str:
        value = env.get(name, "").strip()
        if not value:
            raise ConfigError(f"{name} is not set")
        return value

    hub_url = env.get("PLANT_HUB_URL", "").strip().rstrip("/")
    hub_key = env.get("PLANT_HUB_KEY", "").strip()
    if bool(hub_url) != bool(hub_key):
        raise ConfigError("set PLANT_HUB_URL and PLANT_HUB_KEY together, or neither")
    if hub_url and not hub_url.startswith(("https://", "http://")):
        raise ConfigError("PLANT_HUB_URL is not an http(s) address")
    # The proxy's directory is a source unless only the hub was named: a server given a hub
    # and no PROXY_DIR reads the plant alone, and one given both reads both.
    proxy_dir = env.get("PROXY_DIR", "").strip()
    sources = (("proxy",) if proxy_dir or not hub_url else ()) + (("plant",) if hub_url else ())
    return Settings(
        proxy_dir=Path(proxy_dir or "/proxy-data"),
        boxes=_boxes(env, need, sources),
        sync_seconds=int(env.get("SYNC_SECONDS", "").strip() or 300),
        sources=sources,
        hub_url=hub_url,
        hub_key=hub_key,
        data_dir=Path(env.get("DATA_DIR", "").strip() or "/data"),
    )


def _boxes(env, need, sources: tuple[str, ...]) -> tuple[Target, ...]:
    """BOXES=dev,prod reads <NAME>_API_KEY, <NAME>_BOX_HOST and <NAME>_BOX_REPO for each
    name. Without BOXES there is one box, from API_KEY, BOX_HOST and BOX_REPO.
    <NAME>_SOURCES (or SOURCES, for the one box) names the sources that caller may see,
    e.g. ``proxy``; left out, it sees every source this server reads."""

    def seen(name: str) -> tuple[str, ...]:
        listed = tuple(s.strip().lower() for s in env.get(name, "").split(",") if s.strip())
        for source in listed:
            if source not in sources:
                raise ConfigError(f"{name}: {source!r} is not a source this server reads")
        return listed or sources

    listed = [n.strip().lower() for n in env.get("BOXES", "").split(",") if n.strip()]
    if not listed:
        return (
            Target("default", need("API_KEY"), need("BOX_HOST"), need("BOX_REPO"), seen("SOURCES")),
        )
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
                seen(f"{prefix}_SOURCES"),
            )
        )
    if len({b.name for b in boxes}) != len(boxes):
        raise ConfigError("BOXES names a box twice")
    # A key decides the box, so two boxes with one key would make the choice a guess.
    if len({b.api_key for b in boxes}) != len(boxes):
        raise ConfigError("two boxes share an API key; each needs its own")
    return tuple(boxes)
