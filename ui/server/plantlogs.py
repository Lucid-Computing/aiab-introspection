"""Plant Logs: the RSI plant's exports, read from a folder (MAC-467).

The plant will later answer HTTP GETs (a pull API). Until then this module stands in for
it, reading the plant's export files from the folder AIAB_PLANT_EXPORT names, on a laptop
or on the Render disk. The exports are confidential and never go into git.

A run is any folder holding ``records/*.jsonl``; next to it may sit ``jobs.csv`` and
``network.csv``, and ``scores.csv`` in it or its parent. The functions mirror the API:

    runs(root)               the runs and their sessions
    transcript(path)         the records, already in the box's format
    jobs(run) / network(run) / scores(run)

A records line is already a box record (stream, batch, offset, agent_id, session_id, ts,
kind, content) and is sent as written, with one change: the batch. Every file in the
export starts at batch 0, and the box refuses a (stream, batch, offset) seen twice in one
run, so each records file gets its own batch: its place in the export, oldest first. A
session the plant split in two (one session id, two runs) is two files and two batches.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

LANES = ("Model calls", "Tool calls", "Slurm jobs", "GPU use", "Network")
NETWORK_BIN_SECONDS = 60


@dataclass
class PlantRecord:
    """One record of a plant transcript."""

    id: str  # batch:offset
    time: float
    who: str  # the agent id
    session_id: str
    kind: str  # request, tool_call, tool_result, response
    run: str  # the run folder's path, relative to the export
    box: dict = field(repr=False)  # the record as the box receives it

    @property
    def when(self) -> str:
        return datetime.fromtimestamp(self.time, UTC).strftime("%Y-%m-%d %H:%M:%S")

    @property
    def when_short(self) -> str:
        return datetime.fromtimestamp(self.time, UTC).strftime("%b %d, %I:%M %p")

    @property
    def title_text(self) -> str:
        return ""  # listed by metadata only

    @property
    def parts(self) -> list[tuple[str, str]]:
        return [(self.kind, self.box["content"])]


def _time(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return 0.0


def _read_jsonl(path: Path) -> list[dict]:
    out = []
    with path.open("rb") as f:
        for line in f:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def _read_csv(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---- the pull API, from a folder -----------------------------------------------------------


def record_files(root: Path) -> list[Path]:
    """Every transcript in the export, oldest first: the order that numbers the batches."""
    files = [p for p in root.rglob("records/*.jsonl") if p.is_file()]

    def first(p: Path) -> tuple[str, str]:
        with p.open("rb") as f:
            for line in f:
                if line.strip():
                    try:
                        return str(json.loads(line).get("ts") or ""), str(p)
                    except ValueError:
                        break
        return "", str(p)

    return sorted(files, key=first)


def runs(root: Path) -> list[dict]:
    """The runs in the export: folder, session ids, record count."""
    out = []
    for batch, path in enumerate(record_files(root)):
        rows = transcript(path, batch)
        out.append(
            {
                "run": str(path.parent.parent.relative_to(root)),
                "sessions": sorted({r["session_id"] for r in rows}),
                "records": len(rows),
            }
        )
    return out


def transcript(path: Path, batch: int) -> list[dict]:
    """One transcript's records in the box's format, the batch set to ``batch``."""
    return [{**r, "batch": batch} for r in _read_jsonl(path)]


def jobs(run: Path) -> list[dict]:
    return _read_csv(run / "jobs.csv")


def network(run: Path) -> list[dict]:
    return _read_csv(run / "network.csv")


def scores(run: Path) -> list[dict]:
    return _read_csv(run / "scores.csv") or _read_csv(run.parent / "scores.csv")


# ---- for the pages -------------------------------------------------------------------------


def load_index(root: Path, paths: list[Path]) -> list[PlantRecord]:
    """Every record of the export, oldest first."""
    out = []
    for batch, path in enumerate(paths):
        run = str(path.parent.parent.relative_to(root))
        for r in transcript(path, batch):
            out.append(
                PlantRecord(
                    id=f"{batch}:{r.get('offset')}",
                    time=_time(str(r.get("ts") or "")),
                    who=str(r.get("agent_id") or "unknown"),
                    session_id=str(r.get("session_id") or ""),
                    kind=str(r.get("kind") or ""),
                    run=run,
                    box=r,
                )
            )
    out.sort(key=lambda r: (r.time, r.box["batch"], r.box["offset"]))
    return out


def _clock(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%H:%M")


def timeline(root: Path, records: list[PlantRecord]) -> list[dict]:
    """One lane chart per run of a session, each on its own UTC axis: the session's model
    and tool calls, the run's Slurm jobs, the jobs that opened GPUs, and network traffic
    per minute. Marks carry positions in percent of the axis; nothing of the text."""
    by_run: dict[str, list[PlantRecord]] = defaultdict(list)
    for r in records:
        by_run[r.run].append(r)
    charts = []
    for run, recs in sorted(by_run.items(), key=lambda kv: kv[1][0].time):
        folder = root / run
        start, end = recs[0].time, recs[-1].time
        job_rows = []
        for j in jobs(folder):
            a = _time(j.get("started_utc") or j.get("submitted_utc") or "")
            b = _time(j.get("ended_utc") or "") or a
            if a and b >= start and a <= end:
                job_rows.append((j, a, b))
        end = max([end] + [b for _, _, b in job_rows])
        start = min([start] + [a for _, a, _ in job_rows])
        span = max(end - start, 1.0)

        def at(t: float, start: float = start, span: float = span) -> float:
            return round(min(max((t - start) / span, 0.0), 1.0) * 100, 3)

        def tick(t: float) -> dict:
            return {"left": at(t), "width": 0}

        def bar(a: float, b: float) -> dict:
            return {"left": at(a), "width": max(round(at(b) - at(a), 3), 0.3)}

        bins: dict[int, int] = defaultdict(int)
        for n in network(folder):
            t = _time(n.get("start_utc") or "")
            if start <= t <= end:
                size = 0
                for k in ("bytes_sent", "bytes_received"):
                    try:
                        size += int(float(n.get(k) or 0))
                    except ValueError:
                        pass
                bins[int((t - start) // NETWORK_BIN_SECONDS)] += size
        peak = max(bins.values(), default=0) or 1
        net = [
            {
                **bar(start + i * NETWORK_BIN_SECONDS, start + (i + 1) * NETWORK_BIN_SECONDS),
                "level": max(round(v / peak, 2), 0.1),
            }
            for i, v in sorted(bins.items())
            if v
        ]
        gpu = [
            bar(a, b)
            for j, a, b in job_rows
            if (j.get("gpu_devices_opened") or "0") not in ("", "0")
        ]
        lanes = {
            "Model calls": [tick(r.time) for r in recs if r.kind in ("request", "response")],
            "Tool calls": [tick(r.time) for r in recs if r.kind == "tool_call"],
            "Slurm jobs": [bar(a, b) for _, a, b in job_rows],
            "GPU use": gpu,
            "Network": net,
        }
        counts = {
            "Model calls": len(lanes["Model calls"]),
            "Tool calls": len(lanes["Tool calls"]),
            "Slurm jobs": len(job_rows),
            "GPU use": len(gpu),
        }
        counts = {n: str(c) for n, c in counts.items()}
        counts["Network"] = f"{sum(bins.values()) / 1e6:.1f} MB"
        charts.append(
            {
                "run": run,
                "start": datetime.fromtimestamp(start, UTC).strftime("%Y-%m-%d %H:%M:%S"),
                "end": datetime.fromtimestamp(end, UTC).strftime("%Y-%m-%d %H:%M:%S"),
                "axis": [
                    {"left": at(start + span * k / 4), "label": _clock(start + span * k / 4)}
                    for k in range(5)
                ],
                "lanes": [{"name": n, "count": counts[n], "marks": lanes[n]} for n in LANES],
                "records": len(recs),
                "batch": recs[0].box["batch"],
                "facts": _facts(folder, recs, [j for j, _, _ in job_rows], start, end),
            }
        )
    return charts


def _int(value: object) -> int:
    try:
        return int(float(str(value or 0)))
    except ValueError:
        return 0


def _facts(folder: Path, recs: list[PlantRecord], job_rows: list[dict], start: float, end: float):
    """What the plant's sensors recorded about one run, in numbers: the transcript by kind
    and size, the Slurm jobs as a table, the network by destination, and the models the
    plant scored. Counts, times and sizes only; nothing of the transcript's text."""
    kinds: dict[str, int] = defaultdict(int)
    for r in recs:
        kinds[r.kind] += 1
    job_table = []
    for j in job_rows:
        a = _time(j.get("started_utc") or j.get("submitted_utc") or "")
        b = _time(j.get("ended_utc") or "") or a
        job_table.append(
            {
                "job": j.get("job", ""),
                "state": (j.get("state") or "").lower(),
                "node": j.get("node", ""),
                "gpus": j.get("gpus_given", ""),
                "started": _clock(a) if a else "",
                "seconds": int(b - a) if a else 0,
                "processes": j.get("processes", ""),
                "gpu_opened": j.get("gpu_devices_opened", ""),
                "outlived": (j.get("outlived") or "0") not in ("", "0"),
            }
        )
    dest: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
    for n in network(folder):
        if not start <= _time(n.get("start_utc") or "") <= end:
            continue
        key = (n.get("to", ""), n.get("port", ""), n.get("service") or n.get("proto", ""))
        dest[key][0] += 1
        dest[key][1] += _int(n.get("bytes_sent"))
        dest[key][2] += _int(n.get("bytes_received"))
    top = sorted(dest.items(), key=lambda kv: -(kv[1][1] + kv[1][2]))[:8]
    saved = []
    for row in scores(folder):
        t = _time(row.get("model_saved_utc") or "")
        if t and start <= t <= end + 3600:
            saved.append(row)
    return {
        "kinds": dict(kinds),
        "chars": sum(len(str(r.box.get("content") or "")) for r in recs),
        "jobs": job_table,
        "gpu_seconds": sum(j["seconds"] for j in job_table if j["gpu_opened"] not in ("", "0")),
        "flows": sum(v[0] for v in dest.values()),
        "destinations": len(dest),
        "top": [
            {
                "to": k[0],
                "port": k[1],
                "service": k[2],
                "flows": v[0],
                "sent": v[1],
                "received": v[2],
            }
            for k, v in top
        ],
        "score_columns": list(saved[0].keys()) if saved else [],
        "scores": saved,
    }
