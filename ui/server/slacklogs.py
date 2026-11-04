"""The Slack export as its own kind of record.

Slack Logs is a static copy of the public-channel export: one JSON lines file per channel,
one row per message with ``channel``, ``channel_id``, ``ts``, ``author`` ("Name <email>
(UID)"), ``text``, ``thread_ts`` and ``meta`` (notes the scraper added: "Thread: 4 replies
(latest: …)", "Reactions: heart (1)", "Files: …"). Each row becomes one ``SlackMessage``,
nothing like a proxied call: no model, status, duration or conversation. A session is the
thread a message starts or answers, otherwise its channel and UTC day; the Logs page shows
a session's messages whole, and a run sends each message's text as one record.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

AUTHOR = re.compile(r"^(.*?)\s*(?:<([^>]*)>)?\s*(?:\(([A-Z0-9]+)\))?\s*$")
REPLIES = re.compile(r"^Thread:\s*(\d+)\s+repl")


@dataclass
class SlackMessage:
    """One message of the export."""

    id: str  # channel:ts
    time: float  # the ts, seconds since the epoch
    who: str  # the author's display name
    email: str
    uid: str  # the Slack user id
    channel: str
    ts: str
    text: str
    thread_ts: str | None  # the thread's root ts, for a reply
    meta: list[str] = field(default_factory=list)  # the scraper's notes, as written
    session_id: str = ""

    @property
    def when(self) -> str:
        return datetime.fromtimestamp(self.time, UTC).strftime("%Y-%m-%d %H:%M:%S")

    @property
    def when_short(self) -> str:
        return datetime.fromtimestamp(self.time, UTC).strftime("%b %d, %I:%M %p")

    @property
    def title_text(self) -> str:
        return self.text

    @property
    def replies(self) -> int | None:
        """How many replies the thread this message starts has, from the scraper's note."""
        for note in self.meta:
            m = REPLIES.match(note)
            if m:
                return int(m.group(1))
        return None

    @property
    def notes(self) -> list[str]:
        """The scraper's notes other than the reply count: reactions, files."""
        return [n for n in self.meta if not REPLIES.match(n)]

    @property
    def parts(self) -> list[tuple[str, str]]:
        """What a run sends to the box for this message: its text, as a request."""
        return [("request", self.text)] if self.text.strip() else []


def author(value: str) -> tuple[str, str, str]:
    """ "Kristian Rönn <kristian@lucidcomputing.ai> (U08GWHCA0E6)" -> name, email, uid."""
    m = AUTHOR.match(value or "")
    if not m:
        return (value or "").strip() or "unknown", "", ""
    return (m.group(1) or "").strip() or "unknown", (m.group(2) or "").strip(), m.group(3) or ""


def _time(ts: str) -> float:
    try:
        return float(ts)
    except ValueError:
        return 0.0


def load_index(paths: list[Path]) -> list[SlackMessage]:
    """Every message of the export, oldest first, each with its session: the thread it
    starts or answers (``#channel · root ts``), else its channel and UTC day."""
    out: list[SlackMessage] = []
    for path in paths:
        rows = []
        with path.open("rb") as f:
            for line in f:
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
        threads = {r.get("thread_ts") for r in rows if r.get("thread_ts")}
        for r in rows:
            channel, ts = r.get("channel") or path.stem, str(r.get("ts") or "")
            root = r.get("thread_ts") or (ts if ts in threads else None)
            time = _time(ts)
            if root:
                session = f"#{channel} · {root}"
            else:
                session = f"#{channel} · {datetime.fromtimestamp(time, UTC).strftime('%Y-%m-%d')}"
            who, email, uid = author(r.get("author") or "")
            out.append(
                SlackMessage(
                    id=f"{channel}:{ts}",
                    time=time,
                    who=who,
                    email=email,
                    uid=uid,
                    channel=channel,
                    ts=ts,
                    text=(r.get("text") or "").strip(),
                    thread_ts=r.get("thread_ts") or None,
                    meta=[str(n) for n in r.get("meta") or []],
                    session_id=session,
                )
            )
    out.sort(key=lambda m: m.time)
    return out
