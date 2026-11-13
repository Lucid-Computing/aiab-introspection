"""Feedback from the site: the Feedback form's entries and the post-it notes left on pages.

Each kind is one append-only JSONL file in ``settings.feedback_dir``: ``feedback.jsonl``
for the form, ``notes.jsonl`` for the notes. Marking a note resolved appends a ``resolve``
line for it, so nothing is ever rewritten; reading folds those lines into the notes.
When ``settings.feedback_webhook`` (a Slack incoming webhook) is set, each new entry and
note is also posted there from a background thread; a failure is logged and never shown.

All text here was typed by someone using the site and is shown to other people: the
templates escape it, and the notes script sets it as text, never as HTML.
"""

from __future__ import annotations

import json
import logging
import math
import re
import secrets
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx

from server.config import Settings

log = logging.getLogger(__name__)

TEXT_MAX = 4000  # characters of form feedback
NOTE_MAX = 1000  # characters of a post-it
NAME_MAX = 80
PAGE_MAX = 500
Y_MAX = 200_000  # pixels: further down than any page here
FLASH = {"msg", "err"}  # one-off messages in the query, not part of the page
ANCHOR = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
NOTE_ID = re.compile(r"^[0-9a-f]{12}$")
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def clean_page(value: str | None) -> str:
    """The page an entry was left on: a path on this site with its query, without the
    one-off msg and err. Anything else (another site, ``//host``, ``/\\host``, control
    characters, an overlong value) becomes ``/``."""
    value = (value or "").strip()
    if (
        not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or len(value) > PAGE_MAX
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        return "/"
    parts = urlsplit(value)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in FLASH]
    return (parts.path or "/") + ("?" + urlencode(query) if query else "")


def clean_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def clean_name(value: Any) -> str:
    return " ".join(str(value or "").split())[:NAME_MAX]


def _number(value: Any, low: float, high: float) -> float:
    """A finite number clamped to [low, high]; anything else is the low end. JSON from the
    browser could carry NaN, which would break every reader of the file."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return low
    return round(min(max(float(value), low), high), 4)


def _append(path: Path, entry: dict[str, Any]) -> None:
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n")


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _form_file(settings: Settings) -> Path:
    return settings.feedback_dir / "feedback.jsonl"


def _notes_file(settings: Settings) -> Path:
    return settings.feedback_dir / "notes.jsonl"


# ---- the Feedback form


def record(settings: Settings, user: dict[str, str], page: str, text: str, name: str) -> dict:
    entry = {
        "id": secrets.token_hex(6),
        "time": _now(),
        "user": user["username"],
        "party": user["party"],
        "name": clean_name(name),
        "page": clean_page(page),
        "text": clean_text(text, TEXT_MAX),
    }
    _append(_form_file(settings), entry)
    return entry


def entries(settings: Settings) -> list[dict[str, Any]]:
    """The form's entries, newest first."""
    found = _read(_form_file(settings))[::-1]  # reversed first: same-second ties stay newest first
    return sorted(found, key=lambda e: str(e.get("time", "")), reverse=True)


# ---- post-it notes


def add_note(settings: Settings, user: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    """A note at a spot on a page. The spot is kept two ways: relative to the nearest
    element with an id around the click (``anchor``; ``ax`` a fraction of its width, ``ay``
    pixels below its top), and relative to the page's main column (``x`` a fraction of its
    width, ``y`` pixels below its top) for when that element is missing or hidden."""
    anchor = str(body.get("anchor") or "")
    note = {
        "kind": "note",
        "id": secrets.token_hex(6),
        "time": _now(),
        "user": user["username"],
        "party": user["party"],
        "name": clean_name(body.get("name")),
        "page": clean_page(body.get("page")),
        "text": clean_text(body.get("text"), NOTE_MAX),
        "anchor": anchor if ANCHOR.match(anchor) else "",
        "ax": _number(body.get("ax"), 0.0, 1.0),
        "ay": _number(body.get("ay"), 0.0, Y_MAX),
        "x": _number(body.get("x"), 0.0, 1.0),
        "y": _number(body.get("y"), 0.0, Y_MAX),
    }
    _append(_notes_file(settings), note)
    return {**note, "resolved": False}


def notes(settings: Settings, page: str | None = None) -> list[dict[str, Any]]:
    """Every note (or one page's), newest first, with ``resolved`` as the last resolve
    line for it left it."""
    found: dict[str, dict[str, Any]] = {}
    for item in _read(_notes_file(settings)):
        if item.get("kind") == "note" and isinstance(item.get("id"), str):
            found[item["id"]] = {**item, "resolved": False}
        elif item.get("kind") == "resolve" and item.get("note") in found:
            found[item["note"]]["resolved"] = bool(item.get("resolved"))
    out = [n for n in reversed(found.values()) if page is None or n.get("page") == page]
    return sorted(out, key=lambda n: str(n.get("time", "")), reverse=True)


def resolve_note(
    settings: Settings, user: dict[str, str], note_id: str, resolved: bool
) -> dict[str, Any] | None:
    """Mark a note resolved (or open again). None when there is no such note."""
    if not NOTE_ID.match(note_id):
        return None
    current = next((n for n in notes(settings) if n["id"] == note_id), None)
    if current is None:
        return None
    _append(
        _notes_file(settings),
        {
            "kind": "resolve",
            "note": note_id,
            "resolved": resolved,
            "time": _now(),
            "user": user["username"],
        },
    )
    return {**current, "resolved": resolved}


# ---- Slack (optional)


def _slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_message(entry: dict[str, Any], kind: str) -> str:
    """``*lab* audit3 (Sam) left a note on `/a/bio/sign``, then the text quoted."""
    who = entry["user"] + (f" ({entry['name']})" if entry.get("name") else "")
    what = "left a note on" if kind == "note" else "on"
    head = _slack_escape(f"*{entry['party']}* {who} {what} `{entry['page']}`")
    lines = entry["text"].splitlines() or [""]
    return head + "\n" + "\n".join("> " + _slack_escape(line) for line in lines)


def notify(settings: Settings, entry: dict[str, Any], kind: str) -> None:
    """Post to Slack in the background when a webhook is set; never raises."""
    url = settings.feedback_webhook
    if not url:
        return
    text = slack_message(entry, kind)

    def run() -> None:
        try:
            httpx.post(url, json={"text": text}, timeout=10).raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("feedback: Slack post failed: %s", exc)

    threading.Thread(target=run, daemon=True).start()
