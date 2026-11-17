"""The RSI plant's runs, fetched from the plant's hub and indexed in memory.

Plants send their finished runs to a hub, and the hub answers HTTP GETs to whoever holds a
reader's token (rsi-plant, docs/protocol/read-api-spec.md). With ``PLANT_HUB_URL`` and
``PLANT_HUB_KEY`` set, this server reads the hub instead of a proxy's directory, and gives
the agreement UI the same two things: sessions as metadata, records sealed to the box. So
the plant's text is readable here and on the hub, and nowhere else.

Four of a run's files are fetched, and its report: the wall's record of every model call
(``egress/record.jsonl``), the tap's connection log (``tap/conn.log``), the lab's addresses
(``config/compose.json``) and ``score.json``. They are kept under ``DATA_DIR/hub/<run>/``,
so a restart fetches only what is new. A file that only grows is fetched from where the
copy here ends; the hub says which files those are.

A run gives up to four kinds of session:

    the agents' transcripts   each call the wall recorded, shaped as the logging proxy would
                              have logged it and read by the same rules (``calls.parse``)
    <run>-jobs                its Slurm jobs, one record per job
    <run>-network             every connection on the lab's network, 100 per record
    <run>-scores              the models the plant scored, one record per model

The three tables are sessions of their own so an agreement can read them next to, or instead
of, the transcript. Their agents are ``plant-jobs``, ``plant-network`` and ``plant-scores``.

The plant overwrote the run's keys before sending, and a provider key is never in a lab. A
run whose records hold something shaped like one anyway is held back whole, and the status
says which.

The index holds each call light, as the proxy's does: its metadata, the kind and size of
each part, and where it is in the copy on disk. For a model call that is the stretch of the
wall's record from its request to the end of its reply; for a table's record, the table and
its place in it. ``with_text`` reads them again when a run exports them.

The fetch and the tables follow the agreement UI's ``server/planthub.py`` (pull request 73).
"""

from __future__ import annotations

import http.client
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from logserver import calls
from logserver.calls import Call
from logserver.config import Settings
from logserver.store import StoreError

RAW = "hub"  # under DATA_DIR: the hub's files, one folder per run
RUN_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}~[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}")
# What a run's sessions are made from: the path on the hub.
RECORD, CONNECTIONS, ADDRESSES, SCORES = (
    "egress/record.jsonl",
    "tap/conn.log",
    "config/compose.json",
    "score.json",
)
WANTED = (RECORD, CONNECTIONS, ADDRESSES, SCORES)
MAX_FILE = 64 << 30  # the most of one file this keeps: a long run's record is gigabytes
MAX_REPLY = 64 << 20  # the most one answer may hold; the hub sends a file 4 MiB at a time
PROVIDER_KEY = re.compile(r"\bsk-(?:ant|proj)-[A-Za-z0-9_-]{16,}")
NOT_ID = re.compile(r"[^A-Za-z0-9_-]")  # the box takes ids of letters, digits, _ and - only
NETWORK_ROWS_PER_RECORD = 100


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an answer, never followed: the token would go along to where it points."""

    def redirect_request(self, *_):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _id(text: str) -> str:
    return NOT_ID.sub("_", text)[:128]


class PlantStore:
    """The same four things the proxy's ``Store`` offers the app: ``calls``, ``refresh``,
    ``start`` and ``status``."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.dir = settings.data_dir / RAW
        self._refresh_lock = threading.Lock()  # one fetch at a time, whoever asks
        self._runs: dict[str, tuple[Call, ...]] = {}  # run -> its calls; () for one held back
        self._held_back: set[str] = set()
        self._calls: tuple[Call, ...] = ()
        # Replaced whole, never changed in place, so a request reads it without the lock.
        self.status: dict = {"at": None, "error": None, "result": None, "problems": []}

    def calls(self) -> tuple[Call, ...]:
        """Every call and table record of every run, oldest first. Never asks the hub: this
        is the index the last refresh built."""
        return self._calls

    def with_text(self, wanted: list[Call]) -> list[Call]:
        """The same calls with their text, read again from the copy of their run's files."""
        by_run: dict[str, list[Call]] = {}
        for call in wanted:
            by_run.setdefault(call.run, []).append(call)
        found: dict[str, Call] = {}
        try:
            for run, asked in by_run.items():
                found.update(_texts(run, self.dir / run, asked))
        except OSError as error:
            raise StoreError(f"a plant call's text cannot be read again: {error}") from None
        missing = [c.id for c in wanted if c.id not in found]
        if missing:
            raise StoreError(
                f"{len(missing)} plant records are not where they were; sync and retry"
            )
        return [replace(c, parts=found[c.id].parts, sizes=found[c.id].sizes) for c in wanted]

    def refresh(self) -> dict:
        """Fetch what changed on the hub and rebuild the index.
        Returns {"new", "runs", "held_back", "calls"}."""
        with self._refresh_lock:
            try:
                try:
                    result, problems = self._refresh()
                except OSError as error:  # this server's own disk
                    raise StoreError(f"writing under {self.dir} failed: {error}") from None
            except StoreError as error:
                self.status = {**self.status, "error": str(error)}
                raise
            self.status = {"at": time.time(), "error": None, "result": result, "problems": problems}
            return result

    def start(self) -> None:
        """Fetch now, then every ``sync_seconds``, forever."""

        def loop() -> None:
            while True:
                try:
                    self.refresh()
                except Exception as error:  # the thread must survive anything the hub sends
                    self.status = {**self.status, "error": f"{type(error).__name__}: {error}"}
                time.sleep(self.settings.sync_seconds)

        threading.Thread(target=loop, daemon=True, name="hub-read").start()

    # ---- one refresh ------------------------------------------------------------------

    def _refresh(self) -> tuple[dict, list[dict]]:
        self.dir.mkdir(parents=True, exist_ok=True)
        _, body, _ = self._get("/api/runs")
        ids = [str(r.get("id") or "") for r in _document(body, "/api/runs").get("runs") or []]
        runs = [run for run in ids if RUN_ID.fullmatch(run)]
        problems: list[dict] = []
        new = 0
        for run in runs:
            raw = self.dir / run
            try:
                changed = self._fetch_run(run, raw)
            except StoreError as error:  # this run waits; the others are still read
                problems.append({"file": run, "problem": str(error)})
                if run not in self._runs and raw.is_dir():
                    changed = True  # what an earlier start fetched is still worth showing
                else:
                    continue
            if changed or run not in self._runs:
                found = _run_calls(run, raw)
                self._held_back.discard(run)
                if found is None:
                    self._held_back.add(run)
                    found = ()
                self._runs[run] = found
                new += 1
        for run in sorted(self._held_back & set(runs)):
            problems.append({"file": run, "problem": "held back: its records hold a provider key"})
        # A run the hub no longer lists is kept: the hub only adds.
        self._calls = tuple(
            sorted((c for found in self._runs.values() for c in found), key=lambda c: c.time)
        )
        result = {
            "new": new,
            "runs": len(runs),
            "held_back": sorted(self._held_back),
            "calls": len(self._calls),
        }
        return result, problems

    # ---- the hub's files, copied ------------------------------------------------------

    def _get(self, path: str, headers: dict[str, str] | None = None):
        """One GET of the hub's read API: (status, body, headers). 304 and 416 are answers."""
        request = urllib.request.Request(
            self.settings.hub_url + path,
            headers={"Authorization": f"Bearer {self.settings.hub_key}", **(headers or {})},
        )
        try:
            with _opener.open(request, timeout=60) as response:
                body = response.read(MAX_REPLY + 1)
                if len(body) > MAX_REPLY:
                    raise StoreError(f"the hub's answer for {path} is larger than any it sends")
                return response.status, body, {k.lower(): v for k, v in response.headers.items()}
        except urllib.error.HTTPError as error:
            if error.code in (304, 416):
                return error.code, b"", {k.lower(): v for k, v in error.headers.items()}
            if error.code in (401, 403):
                raise StoreError("the hub refused the reader's token (PLANT_HUB_KEY)") from None
            raise StoreError(f"the hub answered HTTP {error.code} for {path}") from None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
            raise StoreError(f"the hub is not answering: {error}") from None

    def _fetch_run(self, run: str, raw: Path) -> bool:
        """Bring the copy of one run's files in line with the hub's. True if anything changed."""
        state_file = raw / "state.json"
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        seen = state.setdefault("files", {})
        changed = False
        _, body, _ = self._get(f"/api/runs/{run}/files")
        listed = _document(body, f"/api/runs/{run}/files").get("files") or []
        on_hub = {f.get("path"): f for f in listed if isinstance(f, dict)}
        for path in WANTED:
            entry = on_hub.get(path)
            size = entry.get("size") if entry else None
            # A size that is no plain count of bytes is no size, and no file is kept past
            # MAX_FILE.
            if type(size) is not int or not 0 <= size <= MAX_FILE:
                continue
            if entry.get("growth") == "APPEND":
                changed |= self._fetch_grown(run, entry, raw / path)
            else:
                changed |= self._fetch_whole(run, entry, raw / path, seen)
        # The report is worked out by the hub from the run's records, and says when it is
        # the same.
        etag = state.get("report_etag") if (raw / "report.json").is_file() else None
        status, body, headers = self._get(
            f"/api/runs/{run}", {"If-None-Match": etag} if etag else None
        )
        if status == 200:
            _write(raw / "report.json", body)
            state["report_etag"] = headers.get("etag")
            changed = True
        if changed:
            _write(state_file, json.dumps(state).encode())
        return changed

    def _fetch_grown(self, run: str, entry: dict, target: Path) -> bool:
        """A file that only grows: fetched from where the copy here ends. True if it grew."""
        held = target.stat().st_size if target.is_file() else 0
        if held > entry["size"]:  # the hub holds less than this copy: not the same file
            target.unlink()
            held = 0
        grew = False
        target.parent.mkdir(parents=True, exist_ok=True)
        path = f"/api/runs/{run}/files/{urllib.parse.quote(entry['path'])}"
        while held < entry["size"]:
            status, body, _ = self._get(f"{path}?offset={held}")
            body = body[: entry["size"] - held]  # never past what the list said the hub holds
            if status != 200 or not body:
                break
            with target.open("ab") as out:
                out.write(body)
            held += len(body)
            grew = True
        return grew

    def _fetch_whole(self, run: str, entry: dict, target: Path, seen: dict) -> bool:
        """A file that is rewritten whole: fetched again when the hub's differs."""
        now = [entry["size"], entry.get("changed_ns"), entry.get("sha256")]
        if target.is_file() and seen.get(entry["path"]) == now:
            return False
        data, path = b"", f"/api/runs/{run}/files/{urllib.parse.quote(entry['path'])}"
        while len(data) < entry["size"]:
            status, body, _ = self._get(f"{path}?offset={len(data)}")
            if status != 200 or not body:
                break
            data += body
        if len(data) != entry["size"]:
            return False  # not all of it came: the copy here stays, the next fetch asks again
        _write(target, data)
        seen[entry["path"]] = now
        return True


def _document(body: bytes, path: str) -> dict:
    try:
        found = json.loads(body)
    except ValueError:
        found = None
    if not isinstance(found, dict):
        raise StoreError(f"the hub's answer for {path} is not a JSON document")
    return found


def _write(path: Path, data: bytes) -> None:
    """A file put in place whole, so a read after a stop in the middle never sees half."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    os.replace(part, path)


# ---- a run's sessions, made from its files ---------------------------------------------


TABLES = {  # a table's name, and the columns that date a row
    "jobs": ("started_utc", "submitted_utc"),
    "network": ("start_utc",),
    "scores": ("model_saved_utc",),
}


def _report(raw: Path) -> dict:
    try:
        report = json.loads((raw / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report = {}
    return report if isinstance(report, dict) else {}


def _rows(label: str, raw: Path, report: dict) -> tuple[list[str], list[list]]:
    if label == "jobs":
        return _jobs(report)
    return _network(raw) if label == "network" else _scores(raw)


def _run_calls(run: str, raw: Path) -> tuple[Call, ...] | None:
    """One run's transcript calls and table records, each light. None if a provider key is
    in them. A call's text is looked at once, here, and not kept."""
    report = _report(raw)
    found: list[Call] = []

    def keep(made) -> bool:
        for call in made:
            if any(PROVIDER_KEY.search(content) for _, content in call.parts):
                return False
            found.append(call.light())
        return True

    if not keep(_transcript(run, raw, report)):
        return None
    for label in TABLES:  # each after what came before it: a row with no time takes the last
        if not keep(_table(run, label, _rows(label, raw, report), found)):
            return None
    return tuple(found)


def _texts(run: str, raw: Path, wanted: list[Call]) -> dict[str, Call]:
    """The wanted calls of one run with their text, by id, read again from the copy."""
    report = _report(raw)
    found: dict[str, Call] = {}
    asked = [c for c in wanted if c.at[0] == "record"]
    if asked:
        seqs = {c.at[3] for c in asked}
        span = (min(c.at[1] for c in asked), max(c.at[2] for c in asked))
        for call in _transcript(run, raw, report, seqs, span):
            found[call.id] = call
    for label in {c.at[1] for c in wanted if c.at[0] == "table"}:
        for call in _table(run, label, _rows(label, raw, report), []):
            found[call.id] = call
    return found


def _jsonl(path: Path, span: tuple[int, int] | None = None) -> Iterator[tuple[int, int, dict]]:
    """(where the line starts, where it ends, the line) for each line that is a JSON
    object; with a span, only the lines that start inside it."""
    if not path.is_file():
        return
    with path.open("rb") as f:
        start = span[0] if span else 0
        f.seek(start)
        for line in f:
            end = start + len(line)
            if span and start >= span[1]:
                return
            if line.strip():
                try:
                    row = json.loads(line)
                except ValueError:
                    row = None  # a line still being written
                if isinstance(row, dict):
                    yield start, end, row
            start = end


def _wall_calls(
    record: Path, seqs: set[int] | None = None, span: tuple[int, int] | None = None
) -> Iterator[dict]:
    """Each Anthropic Messages call in the wall's record, in order, with where it starts
    (``from``, its request's line) and ends (``to``, after its reply's last line). A call is
    held only until it ends: a long session's record is gigabytes. With ``seqs``, only
    those calls, looked for inside ``span``."""
    going: dict[int, dict] = {}
    end = 0
    for start, end, r in _jsonl(record, span):  # noqa: B007 - end is used after the loop
        kind = r.get("kind")
        if kind == "request":
            path = r.get("path") or ""
            if not path.startswith("/v1/messages") or "count_tokens" in path:
                continue
            if seqs is not None and r.get("seq") not in seqs:
                continue
            headers = {k.lower(): v for k, v in (r.get("headers") or {}).items()}
            going[r["seq"]] = {
                "from": start,
                "seq": r["seq"],
                "src": r.get("src"),
                "started": r["t_ns"] / 1e9,
                "session": headers.get("x-claude-code-session-id"),
                "body": r.get("body") or "",
                "chunks": [],
                "status": None,
                "request_id": None,
            }
        elif r.get("req_seq") in going:
            call = going[r["req_seq"]]
            if kind == "response_head":
                call.update(status=r.get("status"), request_id=r.get("request_id"))
            elif kind == "response_chunk":
                call["chunks"].append(r.get("data") or "")
            elif kind == "response_end":
                call["ended"] = r["t_ns"] / 1e9
                yield {**going.pop(r["req_seq"]), "to": end}
    for call in going.values():  # calls the wall never saw end: the lab stopped mid-reply
        yield {**call, "to": end}


def _transcript(
    run: str,
    raw: Path,
    report: dict,
    seqs: set[int] | None = None,
    span: tuple[int, int] | None = None,
) -> Iterator[Call]:
    """The run's model calls, each as the logging proxy would have logged it, read by the
    proxy's rules, with its text. Who sent one is the gateway's name for the agent, from the
    hub's report. With ``seqs``, only those calls, looked for inside ``span``."""
    who = {
        c["request_id"]: c.get("gateway_identity")
        for c in (report.get("tap") or {}).get("calls") or []
        if isinstance(c, dict) and c.get("request_id")
    }
    for call in _wall_calls(raw / RECORD, seqs, span):
        sender = _id(who.get(call["request_id"]) or f"agent-{call['src']}")
        headers = {"x-claude-code-session-id": call["session"]} if call["session"] else {}
        try:
            parsed = calls.parse(
                {
                    "id": f"{run}:{call['seq']}",
                    "who": sender,
                    "time": call["started"],
                    "status": call["status"],
                    "duration_s": call.get("ended", call["started"]) - call["started"],
                    "request_headers": headers,
                    "request_raw": {"text": call["body"]},
                    "response_raw": {"text": "".join(call["chunks"])},
                }
            )
        except Exception:
            continue  # one odd call must not stop the run, as in the proxy's store
        # A call with no session of its own is its run's and its agent's: two plants can
        # name an agent alike, and the proxy's rule would join them by time.
        session = _id(parsed.session_id) if parsed.session_id else _id(f"{run}-{sender}")
        at = ("record", call["from"], call["to"], call["seq"])
        yield replace(parsed, session_id=session, run=run, at=at)


def _utc(t) -> str:
    if not t:
        return ""
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _time(value) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _table(
    run: str, label: str, table: tuple[list[str], list[list]], before: list[Call]
) -> list[Call]:
    """A table as one session of records, with their text: one per row, or per group of
    network rows. A row with no time of its own takes the one before it."""
    header, rows = table
    clocks = TABLES[label]
    size = NETWORK_ROWS_PER_RECORD if label == "network" else 1
    last = before[-1].time if before else 0.0
    found = []
    for n, i in enumerate(range(0, len(rows), size)):
        group = rows[i : i + size]
        first = dict(zip(header, group[0], strict=True))
        when = next((_time(first[c]) for c in clocks if first.get(c)), 0.0) or last
        last = when
        if size == 1:
            content = ", ".join(f"{k}={v}" for k, v in first.items())
        else:
            content = ",".join(header) + "\n" + "\n".join(",".join(map(str, r)) for r in group)
        found.append(
            Call(
                id=f"{run}:{label}:{n}",
                time=when,
                who=f"plant-{label}",
                status=None,
                duration_s=None,
                session_id=_id(f"{run}-{label}"),
                model=None,
                stop_reason=None,
                size=0,
                message_count=0,
                run=run,
                at=("table", label, n),
                **calls.texts([("tool_result", content)]),
            )
        )
    return found


def _network(raw: Path) -> tuple[list[str], list[list]]:
    try:
        services = json.loads((raw / ADDRESSES).read_text(encoding="utf-8"))["services"]
        name = {
            net["ipv4_address"]: service
            for service, spec in services.items()
            for net in (spec.get("networks") or {}).values()
            if net and net.get("ipv4_address")
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        name = {}
    rows = []
    for _, _, c in _jsonl(raw / CONNECTIONS):
        if "ts" not in c or "id.orig_h" not in c:
            continue
        rows.append(
            [
                _utc(c["ts"]),
                name.get(c["id.orig_h"], c["id.orig_h"]),
                name.get(c.get("id.resp_h"), c.get("id.resp_h", "")),
                c.get("id.resp_p", ""),
                c.get("proto", ""),
                c.get("service", ""),
                round(c.get("duration") or 0, 3),
                c.get("orig_bytes") or 0,
                c.get("resp_bytes") or 0,
                c.get("conn_state", ""),
            ]
        )
    rows.sort(key=lambda row: row[0])
    header = ["start_utc", "from", "to", "port", "proto", "service", "seconds"]
    return header + ["bytes_sent", "bytes_received", "state"], rows


def _jobs(report: dict) -> tuple[list[str], list[list]]:
    sensed = (report.get("sensor") or {}).get("jobs") or {}
    rows = []
    for j in sorted(report.get("jobs") or [], key=lambda j: j["id"]):
        s = sensed.get(str(j["id"])) or {}
        rows.append(
            [
                j["id"],
                j.get("state", ""),
                j.get("nodes", ""),
                j.get("gpus", 0),
                _utc(j.get("submitted")),
                _utc(j.get("started")),
                _utc(j.get("ended")),
                s.get("processes", ""),
                len(s.get("gpu_devices_opened") or []) if s else "",
                s.get("outlived", "") if s else "",
            ]
        )
    header = ["job", "state", "node", "gpus_given", "submitted_utc", "started_utc", "ended_utc"]
    return header + ["processes", "gpu_devices_opened", "outlived"], rows


def _scores(raw: Path) -> tuple[list[str], list[list]]:
    """The scorer's file, one row per model: what the agent's job claimed (``agent_``) next
    to what the plant measured (``plant_``). The names are the scorer's own."""
    try:
        scored = json.loads((raw / SCORES).read_text(encoding="utf-8")).get("jobs") or []
    except (OSError, ValueError, AttributeError):
        scored = []
    own = ("job", "claim", "saved_at", "sha256")
    claims = sorted({k for j in scored for k in (j.get("claim") or {})})
    measured = sorted({k for j in scored for k in j if k not in own})
    rows = [
        [j.get("job", ""), _utc(j.get("saved_at"))]
        + [(j.get("claim") or {}).get(k, "") for k in claims]
        + [j.get(k, "") for k in measured]
        + [j.get("sha256", "")]
        for j in scored
    ]
    header = ["job", "model_saved_utc"] + [f"agent_{k}" for k in claims]
    return header + [f"plant_{k}" for k in measured] + ["model_sha256"], rows
