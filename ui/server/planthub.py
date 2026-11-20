"""Plant Logs from the hub: the RSI plant's runs, read where they are kept.

Plants send their runs to a hub, and the hub answers HTTP GETs to whoever holds a reader's
token (rsi-plant, docs/protocol/read-api-spec.md). With AIAB_PLANT_HUB_URL and
AIAB_PLANT_HUB_KEY set, this server keeps the hub's list of runs and nothing else:

    <logs_dir>/plant/runs.jsonl   the hub's runs, newest first, one line each: metadata only
    <logs_dir>/plant/hub.json     the ETag of the hub's list, so an unchanged list is not sent

``sync`` asks for the list and, for a run that can still change, how many bytes the hub
holds of its files. No file of a run is fetched for the pages, and none is ever kept here:
a 512 MB server with a small disk cannot hold every run, and a long run's record is
gigabytes (MAC-528). ``parts`` gives the pages each run as up to four sessions with no
text: its transcript, its Slurm jobs, its network traffic and its scores.

A run's records are fetched only when an agreement runs on it (``write_records``), and only
the parts chosen. A file comes in the hub's pieces of at most 4 MiB; each call the wall
recorded is shaped as the logging proxy would have logged it, goes through this app's own
parser (``proxylogs``) and is written as box records before the next is read, so memory
holds one piece and one call however long the run is.

A part's records all carry the part's session id, ``<run>-transcript`` and so on: that is
what an agreement's Log Source names, and what the box filters on. The plant overwrote the
run's keys before sending, and a provider key is never in a lab; a run whose records hold
one anyway is not sent.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from server import plantlogs, proxylogs
from server.config import LogSource, Settings
from server.plantlogs import PlantPart
from server.proxylogs import ProxyError

RUNS = "runs.jsonl"  # under the source's folder: the hub's list of runs
STATE = "hub.json"
RUN_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}~[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}")
# What a run's records are made from: the path on the hub.
RECORD, CONNECTIONS, ADDRESSES, SCORES = (
    "egress/record.jsonl",
    "tap/conn.log",
    "config/compose.json",
    "score.json",
)
PART_FILE = {"transcript": RECORD, "network": CONNECTIONS, "scores": SCORES}
# Slurm's accounting of a run, which the hub makes its report's jobs from: the one file that
# says how much there is of them without the report being read.
ACCOUNTING = re.compile(r"logs/[^/]+\.sacct\.json")
MAX_FILE = 64 << 30  # the most of one file this reads: a long run's record is gigabytes
MAX_REPLY = 64 << 20  # the most one answer may hold; the hub sends a file 4 MiB at a time
MAX_WHOLE = 16 << 20  # the most of a document or file read whole: the report, the scores
PROVIDER_KEY = re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}")
SEND_AS = re.compile(r"[^A-Za-z0-9_-]")  # the box takes a name of letters, digits, _ and - only


class HubAway(ProxyError):
    """The hub did not answer, or refused the token: nothing of it can be read now."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an answer, never followed: the token would go along to where it points."""

    def redirect_request(self, *_):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _get(
    source: LogSource, path: str, headers: dict[str, str] | None = None, most: int = MAX_REPLY
):
    """One GET of the hub's read API: (status, body, headers). 304 and 416 are answers."""
    if not source.key:
        raise HubAway(f"no reader's token configured ({source.key_env})")
    request = urllib.request.Request(
        source.url + path, headers={"Authorization": f"Bearer {source.key}", **(headers or {})}
    )
    try:
        with _opener.open(request, timeout=60) as response:
            body = response.read(most + 1)
            if len(body) > most:
                raise ProxyError(
                    f"the hub's answer for {path} is larger than this server reads at once"
                    f" ({most >> 20} MB)"
                )
            return response.status, body, {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as error:
        if error.code in (304, 416):
            return error.code, b"", {k.lower(): v for k, v in error.headers.items()}
        if error.code in (401, 403):
            raise HubAway("the hub refused the reader's token; it may have changed") from None
        raise ProxyError(f"the hub answered HTTP {error.code} for {path}") from None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        raise HubAway(f"the hub is not answering: {error}") from None


def _document(source: LogSource, path: str) -> dict:
    """One of the hub's JSON documents, read whole."""
    _, body, _ = _get(source, path, most=MAX_WHOLE)
    try:
        value = json.loads(body)
    except ValueError:
        raise ProxyError(f"the hub's answer for {path} is not JSON") from None
    if not isinstance(value, dict):
        raise ProxyError(f"the hub's answer for {path} is not a document")
    return value


def _files(source: LogSource, run: str) -> tuple[dict[str, dict], bool]:
    """The files the hub holds of a run, by path, and whether the run is whole there. A
    size that is no plain count of bytes is no file."""
    listing = _document(source, f"/api/runs/{run}/files")
    files = {}
    for f in listing.get("files") or ():
        size = f.get("size") if isinstance(f, dict) else None
        if type(size) is int and 0 <= size <= MAX_FILE and isinstance(f.get("path"), str):
            files[f["path"]] = f
    return files, listing.get("whole") is True


# ---- the list of runs, kept on disk ---------------------------------------------------------


def _entries(root: Path) -> Iterator[dict]:
    """The runs in the list on disk, one at a time."""
    try:
        with (root / RUNS).open("rb") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and RUN_ID.fullmatch(str(entry.get("id") or "")):
                    yield entry
    except OSError:
        return


def _seconds(value) -> float | None:
    return float(value) if type(value) in (int, float) else None


def sync(settings: Settings, source: LogSource) -> dict:
    """Bring the list of runs on disk in line with the hub's. A run that is whole on the
    hub and not going is not asked about again; one the hub cannot read is listed as
    unread and stops no other."""
    root = source.path(settings)
    root.mkdir(parents=True, exist_ok=True)
    held = {e["id"]: e for e in _entries(root)}
    try:
        etag = json.loads((root / STATE).read_text(encoding="utf-8")).get("etag")
    except (OSError, ValueError, AttributeError):
        etag = None
    status, body, headers = _get(
        source, "/api/runs", {"If-None-Match": etag} if etag and held else None
    )
    if status == 304:
        listed = list(held.values())
    else:
        try:
            listed = json.loads(body).get("runs") or []
        except (ValueError, AttributeError):
            raise ProxyError("the hub's list of runs is not JSON") from None
    entries, unread = [], []
    for r in listed:
        run = str(r.get("id") or "") if isinstance(r, dict) else ""
        if not RUN_ID.fullmatch(run):
            continue
        sessions = r.get("sessions")
        entry = {
            "id": run,
            "site": str(r.get("site") or run.partition("~")[0]),
            "state": str(r.get("state") or ""),
            "started": _seconds(r.get("started")),
            "stopped": _seconds(r.get("stopped")),
            "sessions": len(sessions) if isinstance(sessions, list) else sessions,
        }
        old = held.get(run) or {}
        if old.get("whole") and entry["state"] == old.get("state") != "live":
            entry.update(files=old["files"], whole=True)
        else:
            try:
                files, whole = _files(source, run)
                entry.update(files={p: f["size"] for p, f in files.items()}, whole=whole)
            except HubAway:
                raise
            except ProxyError as exc:
                entry["error"] = str(exc)
                unread.append(run)
        entries.append(entry)
    part = root / (RUNS + ".part")
    with part.open("w", encoding="utf-8") as out:
        for entry in entries:
            out.write(json.dumps(entry) + "\n")
    os.replace(part, root / RUNS)
    (root / STATE).write_text(
        json.dumps({"etag": headers.get("etag") or etag}) + "\n", encoding="utf-8"
    )
    new = sum(1 for e in entries if e["id"] not in held)
    said = f"{source.label}: {len(entries)} run(s) on the hub, {new} new."
    if unread:
        said += f" The hub could not read {len(unread)}: {', '.join(unread)}."
    return {"new": new, "runs": len(entries), "unread": unread, "said": said}


def session_id(run: str, part: str) -> str:
    """The session a part of a run is: the run's id in the box's letters, then the part. A
    name too long for the box is cut, with a few letters of its hash so two stay apart."""
    name = SEND_AS.sub("_", run)
    if len(name) + 1 + len(part) > 128:
        name = name[: 118 - len(part)] + "-" + hashlib.sha256(run.encode()).hexdigest()[:8]
    return f"{name}-{part}"


def parts(root: Path) -> list[PlantPart]:
    """Every part of every run in the list on disk, as the pages' records: read from the
    disk each time it is asked for and kept nowhere, so no page leaves the list in memory.
    A part the hub holds nothing of is not listed. The jobs are as large as Slurm's
    accounting of them on the hub, which is more than is sent: a run with none has no jobs."""
    out = []
    for entry in _entries(root):
        if entry.get("error"):
            continue
        run, files = entry["id"], entry.get("files") or {}
        started = entry.get("started") or 0.0
        for part in plantlogs.PARTS:
            if part == "jobs":
                size = sum(n for path, n in files.items() if ACCOUNTING.fullmatch(path))
            else:
                size = files.get(PART_FILE[part])
            if not size:
                continue
            session = session_id(run, part)
            out.append(
                PlantPart(
                    id=session,
                    time=started,
                    end=entry.get("stopped") or started,
                    who=str(entry.get("site") or ""),
                    session_id=session,
                    part=part,
                    run=run,
                    size=size,
                    state=str(entry.get("state") or ""),
                )
            )
    return out


def unread(root: Path) -> list[str]:
    """The runs the hub could not read at the last sync."""
    return [e["id"] for e in _entries(root) if e.get("error")]


# ---- one run's records, fetched when a run sends them ---------------------------------------


def _lines(source: LogSource, run: str, entry: dict) -> Iterator[bytes]:
    """A file of a run, a line at a time, fetched in the hub's pieces: what is held is one
    piece and the line it cut. Never past the size the hub listed; less than it is an error,
    since a run must not be sent in part."""
    size, held, rest = entry["size"], 0, b""
    path = f"/api/runs/{run}/files/{urllib.parse.quote(entry['path'])}"
    while held < size:
        status, body, _ = _get(source, f"{path}?offset={held}")
        body = body[: size - held]
        if status != 200 or not body:
            break
        held += len(body)
        *whole, rest = (rest + body).split(b"\n")
        yield from whole
    if held < size:
        raise ProxyError(f"the hub sent {held} of the {size} bytes of {entry['path']} of {run}")
    if rest.strip():
        yield rest


def _whole(source: LogSource, run: str, entry: dict | None) -> dict:
    """A small JSON file of a run, read whole; {} when the hub holds none."""
    if entry is None:
        return {}
    if entry["size"] > MAX_WHOLE:
        raise ProxyError(f"{entry['path']} of {run} is too large to read at once")
    try:
        value = json.loads(b"\n".join(_lines(source, run, entry)))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _jsonl(lines: Iterable[bytes]) -> Iterator[dict]:
    for line in lines:
        if line.strip():
            try:
                value = json.loads(line)
            except ValueError:
                continue  # a line still being written
            if isinstance(value, dict):
                yield value


def _calls(records: Iterable[dict]) -> Iterator[dict]:
    """Each Anthropic Messages call in the wall's record, in the order they ended, as the
    logging proxy would have logged it. A call is held only until it ends: a long session's
    record is gigabytes."""
    going: dict[int, dict] = {}
    for r in records:
        kind = r.get("kind")
        if kind == "request":
            path = r.get("path") or ""
            if not path.startswith("/v1/messages") or "count_tokens" in path:
                continue
            going[r["seq"]] = {
                "seq": r["seq"],
                "src": r.get("src"),
                "started": r["t_ns"] / 1e9,
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
    return {
        "id": str(call["seq"]),
        "who": who,
        "time": call["started"],
        "method": "POST",
        "path": "/v1/messages",
        "status": call["status"],
        "duration_s": call.get("ended", call["started"]) - call["started"],
        "request_headers": {},
        "request_raw": {"text": call["body"]},
        "response_raw": {"text": "".join(call["chunks"])},
    }


def _transcript(
    source: LogSource, run: str, entry: dict, report: dict, session: str, batch: int
) -> Iterator[dict]:
    """The run's model calls as box records, one call at a time."""
    who = {
        c["request_id"]: c.get("gateway_identity")
        for c in (report.get("tap") or {}).get("calls") or []
        if isinstance(c, dict) and c.get("request_id")
    }
    offset = 0
    for call in _calls(_jsonl(_lines(source, run, entry))):
        sender = who.get(call["request_id"]) or SEND_AS.sub("-", f"agent-{call['src']}")
        request = proxylogs.parse_request(_proxy_entry(call, sender))
        ts = datetime.fromtimestamp(request.time, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        for kind, content in proxylogs.call_parts(request):
            yield {
                "stream": "proxy",
                "batch": batch,
                "offset": offset,
                "agent_id": sender,
                "session_id": session,
                "ts": ts,
                "kind": kind,
                "content": content,
            }
            offset += 1


def _utc(t) -> str:
    if not t:
        return ""
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _rows(header: list[str], rows: Iterable[list]) -> Iterator[dict]:
    """Table rows as an export's CSV gives them: every cell text, nothing for None."""
    for row in rows:
        yield dict(zip(header, ("" if v is None else str(v) for v in row), strict=True))


def _network(lines: Iterable[bytes], addresses: dict) -> Iterator[dict]:
    """The tap's connection log, a row per connection in the log's own order, each address
    the lab gave a service shown by the service's name."""
    services = addresses.get("services")
    name = {
        net["ipv4_address"]: service
        for service, spec in (services if isinstance(services, dict) else {}).items()
        for net in ((spec or {}).get("networks") or {}).values()
        if net and net.get("ipv4_address")
    }
    header = ["start_utc", "from", "to", "port", "proto", "service", "seconds"]
    header += ["bytes_sent", "bytes_received", "state"]
    return _rows(
        header,
        (
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
            for c in _jsonl(lines)
            if "ts" in c and "id.orig_h" in c
        ),
    )


def _jobs(report: dict) -> Iterator[dict]:
    sensed = (report.get("sensor") or {}).get("jobs") or {}
    header = ["job", "state", "node", "gpus_given", "submitted_utc", "started_utc", "ended_utc"]
    header += ["processes", "gpu_devices_opened", "outlived"]
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
    return _rows(header, rows)


def _scores(scores: dict) -> Iterator[dict]:
    """The scorer's file, one row per model: what the agent's job claimed (``agent_``) next to
    what the plant measured (``plant_``). The names are the scorer's own."""
    scored = [j for j in scores.get("jobs") or [] if isinstance(j, dict)]
    own = ("job", "claim", "saved_at", "sha256")
    claims = sorted({k for j in scored for k in (j.get("claim") or {})})
    measured = sorted({k for j in scored for k in j if k not in own})
    header = ["job", "model_saved_utc"] + [f"agent_{k}" for k in claims]
    header += [f"plant_{k}" for k in measured] + ["model_sha256"]
    return _rows(
        header,
        (
            [j.get("job", ""), _utc(j.get("saved_at"))]
            + [(j.get("claim") or {}).get(k, "") for k in claims]
            + [j.get(k, "") for k in measured]
            + [j.get("sha256", "")]
            for j in scored
        ),
    )


def write_records(
    source: LogSource, run: str, wanted: dict[str, tuple[str, int]], out: TextIO
) -> dict[str, int]:
    """Fetch the wanted parts of one run from the hub and write them to ``out`` as box
    records, a line each, as they arrive. ``wanted`` gives each part its session id and its
    batch; the answer is how many records each session got. Nothing else of the run is
    fetched, and nothing is kept but what is written. A record that holds a provider key
    stops the run from being sent: the caller throws ``out`` away."""
    if not RUN_ID.fullmatch(run):
        raise ProxyError(f"{run} is no run of the hub")
    files, _ = _files(source, run)
    report = _document(source, f"/api/runs/{run}") if {"transcript", "jobs"} & set(wanted) else {}
    counts = {}
    for part, (session, batch) in wanted.items():
        if part == "transcript":
            entry = files.get(RECORD)
            records = _transcript(source, run, entry, report, session, batch) if entry else ()
        else:
            if part == "jobs":
                rows = _jobs(report)
            elif part == "scores":
                rows = _scores(_whole(source, run, files.get(SCORES)))
            elif part == "network":
                entry = files.get(CONNECTIONS)
                addresses = _whole(source, run, files.get(ADDRESSES))
                rows = _network(_lines(source, run, entry), addresses) if entry else ()
            else:
                raise ProxyError(f"{part} is no part of a run")
            records = (box for _, box in plantlogs.table_records(part, rows, session, batch))
        counts[session] = 0
        for record in records:
            if PROVIDER_KEY.search(record["content"]):
                raise ProxyError(f"a provider key is in the records of {run}; it is not sent")
            out.write(json.dumps(record) + "\n")
            counts[session] += 1
    return counts
