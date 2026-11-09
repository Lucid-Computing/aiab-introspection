"""The GitHub export as its own kind of record.

GitHub Logs is a static copy of the Lucid-Computing organisation's public repositories
(``server.githubexport`` fetches it): one JSON lines file per repository, one row per post.
A post is an issue or pull request as opened, a comment on one, a review or a review
comment, or a commit on the default branch. Each row has ``repo``, ``kind``, ``id``,
``time`` (ISO 8601, UTC), ``author`` (the GitHub login), ``url``, ``text`` and ``meta``
(short notes: state, files changed, the line a review comment is on), and for a thread
``number`` and ``title``, for a commit ``sha``. Each row becomes one ``GitHubPost``. A
session is the issue or pull request thread a post belongs to, and for commits the
repository and UTC day; a run sends each post's text as one record.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# What a post is, for the Logs page: "opened #12", "commented", ...
KIND_LABELS = {
    "issue": "opened issue",
    "pull": "opened pull request",
    "comment": "commented",
    "review": "reviewed",
    "review_comment": "commented on the code",
    "commit": "committed",
}


@dataclass
class GitHubPost:
    """One post of the export."""

    id: str  # repo:kind:GitHub's id (the sha for a commit)
    time: float  # seconds since the epoch
    who: str  # the GitHub login
    repo: str
    kind: str  # a key of KIND_LABELS
    url: str
    text: str
    number: int | None = None  # the issue or pull request, for a thread post
    title: str = ""  # the issue or pull request's title, for a thread post
    sha: str = ""  # for a commit
    meta: list[str] = field(default_factory=list)
    session_id: str = ""

    @property
    def when(self) -> str:
        return datetime.fromtimestamp(self.time, UTC).strftime("%Y-%m-%d %H:%M:%S")

    @property
    def when_short(self) -> str:
        return datetime.fromtimestamp(self.time, UTC).strftime("%b %d, %I:%M %p")

    @property
    def action(self) -> str:
        """What the author did, as the Logs page says it: "opened pull request #47"."""
        label = KIND_LABELS.get(self.kind, self.kind)
        if self.kind in ("issue", "pull") and self.number is not None:
            return f"{label} #{self.number}"
        if self.kind == "commit" and self.sha:
            return f"{label} {self.sha[:7]}"
        return label

    @property
    def anchor(self) -> str:
        """An id for the post's row on the page, unique within a session."""
        return "".join(c if c.isalnum() else "-" for c in self.id)

    @property
    def title_text(self) -> str:
        """What a session is titled by: the thread's title, or a commit's first line."""
        if self.title:
            return self.title
        return self.text.split("\n", 1)[0]

    @property
    def notes(self) -> list[str]:
        return self.meta

    @property
    def parts(self) -> list[tuple[str, str]]:
        """What a run sends to the box for this post: its text, as a request. An opened
        thread carries its title too, since the body often leans on it."""
        text = self.text.strip()
        if self.kind in ("issue", "pull") and self.title:
            text = f"{self.title}\n\n{text}".strip()
        return [("request", text)] if text else []


def _time(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return 0.0


def load_index(paths: list[Path]) -> list[GitHubPost]:
    """Every post of the export, oldest first, each with its session: ``repo#number`` for a
    thread post, ``repo · commits · day`` for a commit."""
    out: list[GitHubPost] = []
    for path in paths:
        with path.open("rb") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict):
                    continue
                repo = str(r.get("repo") or path.stem)
                kind = str(r.get("kind") or "comment")
                time = _time(str(r.get("time") or ""))
                number = r.get("number")
                if kind == "commit" or number is None:
                    day = datetime.fromtimestamp(time, UTC).strftime("%Y-%m-%d")
                    session = f"{repo} · commits · {day}"
                else:
                    session = f"{repo}#{number}"
                out.append(
                    GitHubPost(
                        id=f"{repo}:{kind}:{r.get('id') or r.get('sha') or ''}",
                        time=time,
                        who=str(r.get("author") or "unknown"),
                        repo=repo,
                        kind=kind,
                        url=str(r.get("url") or ""),
                        text=(r.get("text") or "").strip(),
                        number=number if isinstance(number, int) else None,
                        title=str(r.get("title") or ""),
                        sha=str(r.get("sha") or ""),
                        meta=[str(n) for n in r.get("meta") or []],
                        session_id=session,
                    )
                )
    out.sort(key=lambda p: p.time)
    return out
