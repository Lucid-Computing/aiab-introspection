"""Several stores behind one: the proxy's directory and the RSI plant's hub, read by one
server. Each store keeps reading its own source on its own thread; this joins what they
hold, so the app asks one thing whichever sources are set.

A call says which store holds it (``Call.source``), and its text is asked of that store.
"""

from __future__ import annotations

from logserver.calls import Call
from logserver.store import StoreError


class Sources:
    def __init__(self, stores: dict):
        self.stores = stores  # name ("proxy", "plant") -> its store

    def calls(self) -> tuple[Call, ...]:
        return tuple(c for store in self.stores.values() for c in store.calls())

    def with_text(self, wanted: list[Call]) -> list[Call]:
        found: dict[int, Call] = {}
        for name, store in self.stores.items():
            mine = [(n, c) for n, c in enumerate(wanted) if c.source == name]
            if mine:
                read = store.with_text([c for _, c in mine])
                found.update({n: c for (n, _), c in zip(mine, read, strict=True)})
        if len(found) != len(wanted):
            raise StoreError("a call belongs to a source this server does not read")
        return [found[n] for n in range(len(wanted))]

    def refresh(self) -> dict:
        """Every source read again. One that fails does not stop the others; the failure
        is the error of the whole, after the rest were read."""
        errors = []
        for name, store in self.stores.items():
            try:
                store.refresh()
            except StoreError as error:
                errors.append(f"{name}: {error}")
        if errors:
            raise StoreError("; ".join(errors))
        return self.status["result"]

    def start(self) -> None:
        for store in self.stores.values():
            store.start()

    @property
    def status(self) -> dict:
        """The stores' statuses as one. ``at`` is the oldest last read, and nothing until
        every source was read once. The result keeps the proxy's own fields at the top,
        where the agreement UI looks for them, and each source's whole result by name."""
        each = {name: store.status for name, store in self.stores.items()}
        ats = [s["at"] for s in each.values()]
        results = {name: s["result"] for name, s in each.items()}
        result = None
        if all(r is not None for r in results.values()):
            result = {
                **(results.get("proxy") or {}),
                "new": sum(r.get("new", 0) for r in results.values()),
                "calls": sum(r.get("calls", 0) for r in results.values()),
                **results,
            }
        return {
            "at": None if None in ats else min(ats),
            "error": "; ".join(f"{n}: {s['error']}" for n, s in each.items() if s["error"]) or None,
            "result": result,
            "problems": [{"source": n, **p} for n, s in each.items() for p in s["problems"]],
        }
