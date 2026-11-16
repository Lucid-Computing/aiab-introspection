"""Plant Logs from the hub: the RSI plant's runs, fetched by this server.

Plants send their runs to a hub, and the hub answers HTTP GETs to whoever holds a reader's
token (rsi-plant, docs/protocol/read-api-spec.md). With AIAB_PLANT_HUB_URL and
AIAB_PLANT_HUB_KEY set, ``sync`` keeps the folder ``plantlogs`` reads up to date, so Plant
Logs needs nobody to copy an export by hand:

    <logs_dir>/plant/<run>/records/<session>.jsonl   the transcript, as box records
    <logs_dir>/plant/<run>/jobs.csv                  the run's Slurm jobs
    <logs_dir>/plant/<run>/network.csv               every connection on the lab's network
    <logs_dir>/plant/<run>/scores.csv                the models the plant scored
    <logs_dir>/plant/.hub/<run>/                     the hub's files those are made from

A run is ``<plant>~<folder>``, the hub's own id for it. Four of a run's files are fetched,
and its report: the wall's record of every model call (``egress/record.jsonl``), the tap's
connection log (``tap/conn.log``), the lab's addresses (``config/compose.json``) and
``score.json``. A file that only grows is fetched from where the copy here ends, so a run
that is still going costs only its new bytes; the hub says which files those are.

The transcript goes through this app's own parser (``proxylogs``), unchanged: each call the
wall recorded is shaped as the logging proxy would have logged it, so the records are what
a run on proxy logs would send the box. The plant overwrote the run's keys before sending,
and a provider key is never in a lab; a run whose records hold one anyway is not written.
"""

from __future__ import annotations

import contextlib
import csv
import http.client
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from server import proxylogs
from server.config import LogSource, Settings
from server.proxylogs import ProxyError

RAW = ".hub"  # under the source's folder: the hub's files, one folder per run
RUN_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}~[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}")
# What a run's export is made from: the path on the hub, and whether it only grows there.
RECORD, CONNECTIONS, ADDRESSES, SCORES = (
    "egress/record.jsonl",
    "tap/conn.log",
    "config/compose.json",
    "score.json",
)
WANTED = (RECORD, CONNECTIONS, ADDRESSES, SCORES)
MAX_FILE = 64 << 30  # the most of one file this keeps: a long run's record is gigabytes
MAX_REPLY = 64 << 20  # the most one answer may hold; the hub sends a file 4 MiB at a time
PROVIDER_KEY = re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}")
SEND_AS = re.compile(r"[^A-Za-z0-9_-]")  # the box takes a name of letters, digits, _ and - only


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an answer, never followed: the token would go along to where it points."""

    def redirect_request(self, *_):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _get(source: LogSource, path: str, headers: dict[str, str] | None = None):
    """One GET of the hub's read API: (status, body, headers). 304 and 416 are answers."""
    if not source.key:
        raise ProxyError(f"no reader's token configured ({source.key_env})")
    request = urllib.request.Request(
        source.url + path, headers={"Authorization": f"Bearer {source.key}", **(headers or {})}
    )
    try:
        with _opener.open(request, timeout=60) as response:
            body = response.read(MAX_REPLY + 1)
            if len(body) > MAX_REPLY:
                raise ProxyError(f"the hub's answer for {path} is larger than any it should send")
            return response.status, body, {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as error:
        if error.code in (304, 416):
            return error.code, b"", {k.lower(): v for k, v in error.headers.items()}
        if error.code in (401, 403):
            raise ProxyError("the hub refused the reader's token; it may have changed") from None
        raise ProxyError(f"the hub answered HTTP {error.code} for {path}") from None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        raise ProxyError(f"the hub is not answering: {error}") from None


def _write(path: Path, data: bytes) -> None:
    """A file put in place whole, so a page reading the folder never sees half of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    os.replace(part, path)


# ---- the hub's files, copied --------------------------------------------------------------


def _fetch_grown(source: LogSource, run: str, entry: dict, target: Path) -> bool:
    """A file that only grows: fetched from where the copy here ends. True if it grew."""
    held = target.stat().st_size if target.is_file() else 0
    if held > entry["size"]:  # the hub holds less than this copy: not the same file any more
        target.unlink()
        held = 0
    grew = False
    target.parent.mkdir(parents=True, exist_ok=True)
    path = f"/api/runs/{run}/files/{urllib.parse.quote(entry['path'])}"
    while held < entry["size"]:
        status, body, _ = _get(source, f"{path}?offset={held}")
        body = body[: entry["size"] - held]  # never past what the list said the hub holds
        if status != 200 or not body:
            break
        with target.open("ab") as out:
            out.write(body)
        held += len(body)
        grew = True
    return grew


def _fetch_whole(source: LogSource, run: str, entry: dict, target: Path, seen: dict) -> bool:
    """A file that is rewritten whole: fetched again when the hub's differs. True if it did."""
    now = [entry["size"], entry["changed_ns"], entry.get("sha256")]
    if target.is_file() and seen.get(entry["path"]) == now:
        return False
    data, path = b"", f"/api/runs/{run}/files/{urllib.parse.quote(entry['path'])}"
    while len(data) < entry["size"]:
        status, body, _ = _get(source, f"{path}?offset={len(data)}")
        if status != 200 or not body:
            break
        data += body
    if len(data) != entry["size"]:
        return False  # not all of it came: the copy here stays, and the next sync asks again
    _write(target, data)
    seen[entry["path"]] = now
    return True


def _fetch_run(source: LogSource, run: str, raw: Path) -> bool:
    """Bring the copy of one run's files in line with the hub's. True if anything changed."""
    state_file = raw / "state.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    seen = state.setdefault("files", {})
    changed = False
    _, body, _ = _get(source, f"/api/runs/{run}/files")
    on_hub = {f.get("path"): f for f in json.loads(body).get("files", []) if isinstance(f, dict)}
    for path in WANTED:
        entry = on_hub.get(path)
        size = entry.get("size") if entry else None
        # A size that is no plain count of bytes is no size, and no file the export is made
        # from is kept past MAX_FILE.
        if type(size) is not int or not 0 <= size <= MAX_FILE:
            continue
        if entry.get("growth") == "APPEND":
            changed |= _fetch_grown(source, run, entry, raw / path)
        else:
            changed |= _fetch_whole(source, run, entry, raw / path, seen)
    # The report is worked out by the hub from the run's records, and says when it is the same.
    etag = state.get("report_etag") if (raw / "report.json").is_file() else None
    status, body, headers = _get(
        source, f"/api/runs/{run}", {"If-None-Match": etag} if etag else None
    )
    if status == 200:
        _write(raw / "report.json", body)
        state["report_etag"] = headers.get("etag")
        changed = True
    if changed:
        _write(state_file, json.dumps(state).encode())
    return changed


# ---- the export, made from them -----------------------------------------------------------


def _jsonl(path: Path) -> Iterator[dict]:
    if not path.is_file():
        return
    with path.open("rb") as f:
        for line in f:
            if line.strip():
                try:
                    yield json.loads(line)
                except ValueError:
                    continue  # a line still being written


def _calls(record: Path) -> Iterator[dict]:
    """Each Anthropic Messages call in the wall's record, in order, as the logging proxy would
    have logged it. A call is held only until it ends: a long session's record is gigabytes."""
    going: dict[int, dict] = {}
    for r in _jsonl(record):
        kind = r.get("kind")
        if kind == "request":
            path = r.get("path") or ""
            if not path.startswith("/v1/messages") or "count_tokens" in path:
                continue
            headers = {k.lower(): v for k, v in (r.get("headers") or {}).items()}
            going[r["seq"]] = {
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
                yield going.pop(r["req_seq"])
    yield from going.values()  # calls the wall never saw end: the lab stopped mid-reply


def _proxy_entry(call: dict, who: str) -> dict:
    headers = {"x-claude-code-session-id": call["session"]} if call["session"] else {}
    return {
        "id": str(call["seq"]),
        "who": who,
        "time": call["started"],
        "method": "POST",
        "path": "/v1/messages",
        "status": call["status"],
        "duration_s": call.get("ended", call["started"]) - call["started"],
        "request_headers": headers,
        "request_raw": {"text": call["body"]},
        "response_raw": {"text": "".join(call["chunks"])},
    }


def _utc(t) -> str:
    if not t:
        return ""
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _table(path: Path, table: tuple[list[str], list[list]]) -> None:
    """A CSV put in place, or taken away when the run has no such rows."""
    header, rows = table
    if not rows:
        path.unlink(missing_ok=True)
        return
    part = path.with_name(path.name + ".part")
    with part.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    os.replace(part, path)


def _records(raw: Path, report: dict) -> dict[str, bytes] | None:
    """The run's transcripts, one file per session. None if a provider key is in them."""
    who = {
        c["request_id"]: c.get("gateway_identity")
        for c in (report.get("tap") or {}).get("calls") or []
        if c.get("request_id")
    }
    requests = []
    for call in _calls(raw / RECORD):
        sender = who.get(call["request_id"]) or SEND_AS.sub("-", f"agent-{call['src']}")
        requests.append(proxylogs._light(proxylogs.parse_request(_proxy_entry(call, sender))))
    written: dict[str, bytes] = {}
    for session in proxylogs.sessions(requests):
        lines = proxylogs.to_box_records(session.records)
        text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in lines)
        if PROVIDER_KEY.search(text):
            return None
        written[SEND_AS.sub("_", session.id) + ".jsonl"] = text.encode()
    return written


def _network(raw: Path) -> tuple[list[str], list[list]]:
    try:
        services = json.loads((raw / ADDRESSES).read_text(encoding="utf-8"))["services"]
    except (OSError, ValueError, KeyError):
        services = {}
    name = {
        net["ipv4_address"]: service
        for service, spec in services.items()
        for net in (spec.get("networks") or {}).values()
        if net and net.get("ipv4_address")
    }
    rows = []
    for c in _jsonl(raw / CONNECTIONS):
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
    """The scorer's file, one row per model: what the agent's job claimed (``agent_``) next to
    what the plant measured (``plant_``). The names are the scorer's own."""
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


def export(raw: Path, out: Path, lock=None) -> bool:
    """Make one run's export folder from the copy of its files. False if it was held back.
    Everything is worked out first; the folder changes only under ``lock``, the one the pages
    hold while they list and read it, so a page never finds a file gone that it just listed."""
    try:
        report = json.loads((raw / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report = {}
    records = _records(raw, report)
    tables = {"network.csv": _network(raw), "jobs.csv": _jobs(report), "scores.csv": _scores(raw)}
    with lock or contextlib.nullcontext():
        folder = out / "records"
        folder.mkdir(parents=True, exist_ok=True)
        for name, data in (records or {}).items():
            _write(folder / name, data)
        for stale in folder.glob("*.jsonl"):
            if stale.name not in (records or {}):
                stale.unlink()
        if records is None:
            return False
        for name, table in tables.items():
            _table(out / name, table)
    return True


# ---- sync ---------------------------------------------------------------------------------


def sync(settings: Settings, source: LogSource) -> dict:
    """Fetch what changed on the hub and make each changed run's export again."""
    root = source.path(settings)
    root.mkdir(parents=True, exist_ok=True)
    _, body, _ = _get(source, "/api/runs")
    ids = [str(r.get("id") or "") for r in json.loads(body).get("runs") or []]
    runs = [run for run in ids if RUN_ID.fullmatch(run)]
    new, held_back = 0, []
    lock = proxylogs._state(source)["index_lock"]  # held by the pages while they read the folder
    for run in runs:
        raw, out = root / RAW / run, root / run
        if _fetch_run(source, run, raw) or not out.is_dir():
            if export(raw, out, lock):
                new += 1
            else:
                held_back.append(run)
    said = f"{source.label}: {len(runs)} run(s) on the hub, {new} fetched or changed."
    if held_back:
        said += f" Not shown, a provider key is in the records: {', '.join(held_back)}."
    return {"new": new, "runs": len(runs), "held_back": held_back, "said": said}
