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
            }
        )
    return charts
