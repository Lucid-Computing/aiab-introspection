"""The Proxy key page (MAC-478): logging-proxy keys for people who signed in here.

The logging proxy hands out keys at ``POST /_signup {"name", "code"}``. Its code is held by
this server, so a person whose verified email is on ``AIAB_PROXY_USERS`` can get a key
without ever seeing the code: they pick a name, this server asks the proxy, and the key is
shown to them once. The proxy keeps only the key's hash; this server keeps neither, only a
record of which email got which name (``proxy-keys.jsonl``), so an admin can find a
person's names when taking their access away. Revoking a key is the proxy's
``./users.sh revoke <name>`` for now (MAC-479).
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

# The proxy's own rule for names (keys.py NAME_RE); it compares them case-insensitively.
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class Outcome:
    """What the proxy answered: the key, or a message to show instead."""

    key: str | None = None
    error: str | None = None


def suggest_name(email: str) -> str:
    """A starting point for the name field: the part before @, with anything the proxy
    would refuse turned into -, cut to 32 characters."""
    local = email.split("@", 1)[0]
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", local).strip("-")[:32].strip("-")
    return name or "me"


def names_for(record_file: Path, email: str) -> list[str]:
    """The names this email already got, oldest first."""
    if not record_file.is_file():
        return []
    names: list[str] = []
    for line in record_file.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("email") != email:
            continue  # a damaged line must not break the page for everyone
        name = row.get("name")
        if isinstance(name, str) and name not in names:
            names.append(name)
    return names


def record(record_file: Path, email: str, name: str) -> None:
    """Note that this email got this name. Never the key. Owner-only, like accounts.json:
    it lists people's emails."""
    record_file.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"time": int(time.time()), "email": email, "name": name})
    fd = os.open(record_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def request_key(url: str, code: str, name: str, *, transport: Any | None = None) -> Outcome:
    """Ask the proxy at ``url`` for a key under ``name``. Never raises; the code and the key
    are never logged."""
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, transport=transport) as client:
            response = client.post(f"{url}/_signup", json={"name": name, "code": code})
    except httpx.TimeoutException:
        return Outcome(
            error="The logging proxy took too long to answer. It may have made a key that never"
            " reached you, so that name may now be taken: try another name, or ask an admin"
            " to revoke it."
        )
    except httpx.HTTPError:
        return Outcome(error="The logging proxy could not be reached. Try again in a minute.")
    if response.status_code == 200:
        try:
            body = response.json()
        except ValueError:
            body = None
        key = body.get("key") if isinstance(body, dict) else None
        if isinstance(key, str) and key:
            return Outcome(key=key)
        return Outcome(error="The logging proxy answered, but without a key. Ask an admin.")
    if response.status_code == 409:
        return Outcome(error=f"The name {name} is taken on the logging proxy. Pick another.")
    if response.status_code == 400:
        return Outcome(error="The logging proxy refused that name. Use letters, digits, - and _.")
    if response.status_code in (401, 404):
        return Outcome(
            error="This server's signup code for the logging proxy is wrong or signup is off"
            " there. Ask an admin to check AIAB_SIGNUP_CODE."
        )
    return Outcome(error=f"The logging proxy answered {response.status_code}. Try again later.")
