"""Run-local evidence capabilities; no external retrieval or ambient Python tools."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import signal
import sys
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[2] / "agent_runtime"
MAX_FRAME = 2_097_152
TOOL_SPECS = [
    {
        "name": "search",
        "description": (
            'Search authorized evidence. request must encode {"query":"substring",'
            '"filters":{"limit":100,"offset":0}}. query is a string, not an object '
            "or list. filters is optional. Pagination belongs inside filters. "
            "Use SQL for boolean or structured queries."
        ),
    },
    {"name": "fetch", "description": "Fetch immutable authorized events. Parameters: ids (array)."},
    {"name": "sql", "description": "Read-only SQLite over scoped events. Parameters: sql."},
    {
        "name": "python",
        "description": (
            "Isolated Python over explicit event IDs, no files/network. "
            "Parameters: ids, code. Set result."
        ),
    },
    {
        "name": "join",
        "description": (
            "Exact or temporal join over event IDs. Parameters: left_ids, right_ids, "
            "on, right_on (optional), within_seconds (optional)."
        ),
    },
    {
        "name": "coverage",
        "description": (
            "Observed collection coverage and actually searched/examined IDs. Parameters: none."
        ),
    },
    {
        "name": "counterevidence",
        "description": (
            'Search alternative explanations. request encodes {"query":"substring",'
            '"filters":{"limit":100,"offset":0}}. query must be a string; filters '
            "is optional. This records a search, not proof that alternatives are absent."
        ),
    },
]


class RuntimeFailure(ValueError):
    """Fixed safe code; never include raw model/evidence exception text."""


async def kill_process(process):
    # Descendants can survive an already exited group leader.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await process.wait()


async def isolated_operation(operation, events, **parameters):
    # Bound each JSONL frame and total ingestion independently of output limits.
    frames = [
        json.dumps({"operation": operation, "event_count": len(events), **parameters}).encode()
    ]
    frames.extend(json.dumps(event, allow_nan=False).encode() for event in events)
    if (
        any(len(f) + 1 > MAX_FRAME for f in frames)
        or sum(len(f) + 1 for f in frames) > 16 * 1024**2
    ):
        raise RuntimeFailure("analysis_input_limit")
    # No inherited descriptors, environment, keys, user site or working directory.
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        str(RUNTIME / "python_worker.py"),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={},
        cwd="/tmp",
        start_new_session=True,
        limit=MAX_FRAME + 1,
    )
    try:
        async with asyncio.timeout(5):
            for frame in frames:
                process.stdin.write(frame + b"\n")
                await process.stdin.drain()
            process.stdin.close()
            # Bounded reads prevent output-memory attacks before the worker's check.
            output = bytearray()
            while chunk := await process.stdout.read(8192):
                output.extend(chunk)
                if len(output) > 262144:
                    raise RuntimeFailure(operation + "_failed")
            if await process.wait() != 0:
                raise RuntimeFailure(operation + "_failed")
            return json.loads(output)
    except (TimeoutError, ValueError):
        raise RuntimeFailure(operation + "_failed") from None
    finally:
        await kill_process(process)


async def isolated_python(code, events):
    if not isinstance(code, str) or len(code) > 32000:
        raise RuntimeFailure("invalid_python")
    return await isolated_operation("python", events, code=code)


def events_from(store):
    # The evidence interface promises scoped event access. Never fall back to original data.
    if hasattr(store, "iter_event_pages"):
        return [event for page in store.iter_event_pages() for event in page]
    events = getattr(store, "events", None)
    if events is None:
        events = getattr(store, "scoped_events", None)
    if callable(events):
        events = events()
    if not isinstance(events, list):
        raise RuntimeFailure("evidence_store_events_unavailable")
    return copy.deepcopy(events)


class ToolBroker:
    def __init__(self, store, events=None, authorize=None):
        self.store = store
        self.authorize = authorize
        self.events = events_from(store) if events is None else copy.deepcopy(events)
        self.by_id = {e["id"]: e for e in self.events}
        if len(self.by_id) != len(self.events):
            raise RuntimeFailure("duplicate_event_id")
        self.examined = set()
        self.searched = set()
        self.countersearches = 0

    def fetch(self, ids):
        if (
            not isinstance(ids, list)
            or len(ids) > 1000
            or any(not isinstance(i, str) or i not in self.by_id for i in ids)
        ):
            raise RuntimeFailure("unauthorized_evidence_reference")
        result = [copy.deepcopy(self.by_id[i]) for i in ids]
        if len(json.dumps(result).encode()) > 262144:
            raise RuntimeFailure("tool_output_limit")
        if hasattr(self.store, "fetch"):
            self.store.fetch(ids)
        self.examined.update(ids)
        return result

    def coverage(self):
        return {
            "source_coverage": self.store.coverage(),
            "authorized_events": len(self.by_id),
            "searched_ids": sorted(self.searched),
            "examined_ids": sorted(self.examined),
            "counterevidence_searches": self.countersearches,
            "negative_claims_verified": False,
        }

    async def call(self, name, args):
        if self.authorize is not None:
            await self.authorize()
        if not isinstance(args, dict):
            raise RuntimeFailure("invalid_tool_arguments")
        if name in {"search", "counterevidence"}:
            if set(args) - {"query", "filters"} or not isinstance(args.get("query"), str):
                raise RuntimeFailure("invalid_search")
            filters = args.get("filters", {})
            if not isinstance(filters, dict) or "examine" in filters:
                raise RuntimeFailure("invalid_search")
            if hasattr(self.store, "iter_event_pages"):
                result = self.store.search(args["query"], examine=False, **filters)
            else:
                result = self.store.search(args["query"], **filters)
            # Search projections are not counted as fully examined event content.
            self.searched.update(
                self.store._searched
                if hasattr(self.store, "_searched")
                else (e["id"] for e in result)
            )
            if name == "counterevidence":
                self.countersearches += 1
        elif name == "fetch":
            if set(args) != {"ids"}:
                raise RuntimeFailure("invalid_fetch")
            result = self.fetch(args["ids"])
        elif name == "sql":
            if set(args) != {"sql"} or not isinstance(args["sql"], str):
                raise RuntimeFailure("invalid_sql")
            result = await isolated_operation("sql", self.events, sql=args["sql"])
        elif name == "python":
            if set(args) != {"code", "ids"}:
                raise RuntimeFailure("invalid_python")
            result = await isolated_python(args["code"], self.fetch(args["ids"]))
        elif name == "join":
            result = self.join(args)
        elif name == "coverage":
            if args:
                raise RuntimeFailure("invalid_coverage")
            result = self.coverage()
        else:
            raise RuntimeFailure("forbidden_tool")
        encoded = json.dumps(result, allow_nan=False).encode()
        if len(encoded) > 262144:
            raise RuntimeFailure("tool_output_limit")  # never silently truncate evidence
        return result

    def join(self, args):
        from datetime import datetime

        allowed = {"left_ids", "right_ids", "on", "right_on", "within_seconds"}
        if (
            set(args) - allowed
            or not {"left_ids", "right_ids", "on"} <= set(args)
            or args["on"]
            not in {
                "actor",
                "agent_id",
                "session_id",
                "campaign_id",
                "project_id",
                "native_id",
                "id",
            }
        ):
            raise RuntimeFailure("invalid_join")
        right_on = args.get("right_on", args["on"])
        if right_on not in {
            "actor",
            "agent_id",
            "session_id",
            "campaign_id",
            "project_id",
            "native_id",
            "id",
        }:
            raise RuntimeFailure("invalid_join")
        within = args.get("within_seconds")
        if within is not None and (type(within) not in (int, float) or not 0 <= within <= 31536000):
            raise RuntimeFailure("invalid_join_window")
        left, right = self.fetch(args["left_ids"]), self.fetch(args["right_ids"])
        if len(left) * len(right) > 100000:
            raise RuntimeFailure("join_budget")
        rows = []
        for a in left:
            for b in right:
                if a.get(args["on"]) is None or a.get(args["on"]) != b.get(right_on):
                    continue
                if within is not None:
                    try:
                        delta = abs(
                            (
                                datetime.fromisoformat(a["occurred_at"])
                                - datetime.fromisoformat(b["occurred_at"])
                            ).total_seconds()
                        )
                    except (KeyError, ValueError, TypeError):
                        raise RuntimeFailure("join_unknown_chronology") from None
                    if delta > within:
                        continue
                rows.append({"left_id": a["id"], "right_id": b["id"]})
        return {"rows": rows, "method": "exact" if within is None else "exact-temporal"}
