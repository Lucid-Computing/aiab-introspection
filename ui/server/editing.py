"""Who is editing which agreement right now.

Several people share one UI. Editing is first come, first served: the person who presses
Edit holds the agreement until they press Finish editing or stay idle for a while; others
see the plan read-only with a note saying who has it. Leases live in memory, which is
right for one server; a restart simply frees everything."""

from __future__ import annotations

import time

LEASE_SECONDS = 15 * 60  # idle this long and someone else may take over

_leases: dict[str, dict] = {}


def holder(aid: str) -> dict | None:
    """Who holds the agreement, or None when nobody does (or the lease expired)."""
    lease = _leases.get(aid)
    if lease and time.time() - lease["last"] > LEASE_SECONDS:
        del _leases[aid]
        return None
    return lease


def _same(lease: dict | None, actor: dict) -> bool:
    return bool(lease) and lease["user"] == actor.get("username")


def acquire(aid: str, actor: dict) -> dict | None:
    """Take or refresh the lease for this actor. Returns the other holder when it is taken."""
    lease = holder(aid)
    if lease and not _same(lease, actor):
        return lease
    now = time.time()
    _leases[aid] = {
        "user": actor.get("username", "?"),
        "party": actor.get("party", "?"),
        "since": lease["since"] if lease else now,
        "last": now,
    }
    return None


def blocked_by(aid: str, actor: dict) -> dict | None:
    """The other person holding this agreement, if any; refreshes an own lease."""
    lease = holder(aid)
    if lease is None:
        return None
    if _same(lease, actor):
        lease["last"] = time.time()
        return None
    return lease


def release(aid: str, actor: dict) -> None:
    if _same(holder(aid), actor):
        del _leases[aid]


def describe(lease: dict) -> str:
    since = time.strftime("%H:%M", time.localtime(lease["since"]))
    return f"{lease['user']} ({lease['party']}) is editing this agreement since {since}"
