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

LINES = ("Model calls", "Tool calls", "Jobs running")
CHART_BINS = 40


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
    out.extend(_tables(root, paths, len(paths)))
    out.sort(key=lambda r: (r.time, r.box["batch"], r.box["offset"]))
    return out


NETWORK_ROWS_PER_RECORD = 100
TABLES = (  # file, the name its session carries, the column that dates a row
    ("jobs.csv", "jobs", ("started_utc", "submitted_utc")),
    ("network.csv", "network", ("start_utc",)),
    ("scores.csv", "scores", ("model_saved_utc",)),
)


def _tables(root: Path, paths: list[Path], batch: int) -> list[PlantRecord]:
    """A run's other logs as sessions of their own, so an agreement can read them next to,
    or instead of, the transcript: its Slurm jobs, its network traffic and its scores. One
    record per job or score row; network rows go in groups, as there are thousands. Each
    table is one session named ``<run>-jobs``, ``<run>-network`` or ``<folder>-scores``."""
    out: list[PlantRecord] = []
    seen: set[Path] = set()
    for run in sorted({p.parent.parent for p in paths}):
        for name, label, clocks in TABLES:
            path = run / name if (run / name).is_file() else run.parent / name
            if not path.is_file() or path.resolve() in seen:
                continue
            seen.add(path.resolve())
            rows = _read_csv(path)
            if not rows:
                continue
            size = NETWORK_ROWS_PER_RECORD if label == "network" else 1
            session = f"{path.parent.name}-{label}"
            for offset, i in enumerate(range(0, len(rows), size)):
                group = rows[i : i + size]
                when = next((_time(group[0].get(c) or "") for c in clocks if group[0].get(c)), 0.0)
                when = when or (out[-1].time if out else 0.0)
                if size == 1:
                    content = ", ".join(f"{k}={v}" for k, v in group[0].items())
                else:
                    content = (
                        ",".join(group[0]) + "\n" + "\n".join(",".join(r.values()) for r in group)
                    )
                box = {
                    "stream": "proxy",
                    "batch": batch,
                    "offset": offset,
                    "agent_id": f"plant-{label}",
                    "session_id": session,
                    "ts": datetime.fromtimestamp(when, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "kind": "tool_result",
                    "content": content,
                }
                out.append(
                    PlantRecord(
                        id=f"{batch}:{offset}",
                        time=when,
                        who=f"plant-{label}",
                        session_id=session,
                        kind="tool_result",
                        run=str(run.relative_to(root)),
                        box=box,
                    )
                )
            batch += 1
    return out


def _clock(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%H:%M")


def timeline(root: Path, records: list[PlantRecord]) -> list[dict]:
    """One line chart per run of a session, on its own UTC axis: model calls, tool calls
    and Slurm jobs running, counted in CHART_BINS equal steps of time. Points are in the
    chart's own 1000 x 200 drawing units; nothing of the text."""
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
        step = span / CHART_BINS

        def which(t: float, start: float = start, step: float = step) -> int:
            return min(max(int((t - start) // step), 0), CHART_BINS - 1)

        series = {name: [0] * CHART_BINS for name in LINES}
        for r in recs:
            if r.kind in ("request", "response"):
                series["Model calls"][which(r.time)] += 1
            elif r.kind == "tool_call":
                series["Tool calls"][which(r.time)] += 1
        for _, a, b in job_rows:
            for i in range(which(a), which(b) + 1):
                series["Jobs running"][i] += 1
        totals = {
            "Model calls": sum(series["Model calls"]),
            "Tool calls": sum(series["Tool calls"]),
            "Jobs running": len(job_rows),
        }
        peak = max(max(v) for v in series.values()) or 1
        lines = [
            {
                "name": name,
                "count": totals[name],
                "points": " ".join(
                    f"{40 + 950 * (i + 0.5) / CHART_BINS:.1f},{170 - 150 * v / peak:.1f}"
                    for i, v in enumerate(series[name])
                ),
            }
            for name in LINES
        ]
        charts.append(
            {
                "run": run,
                "start": datetime.fromtimestamp(start, UTC).strftime("%Y-%m-%d %H:%M:%S"),
                "end": datetime.fromtimestamp(end, UTC).strftime("%Y-%m-%d %H:%M:%S"),
                "axis": [
                    {"x": 40 + 950 * k / 4, "label": _clock(start + span * k / 4)} for k in range(5)
                ],
                "lines": lines,
                "peak": peak,
                "step": f"{step / 60:.0f} min" if step >= 90 else f"{step:.0f} s",
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
