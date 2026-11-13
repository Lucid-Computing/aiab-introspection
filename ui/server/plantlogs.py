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

TABLE_AGENTS = {"plant-jobs": "Slurm jobs", "plant-network": "Network", "plant-scores": "Scores"}
KIND_LINES = {  # the chart's lines for a transcript, in this order
    "request": "Requests",
    "tool_call": "Tool calls",
    "tool_result": "Tool results",
    "response": "Responses",
}
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
    """One chart per run of a session, a line per kind of record: how many were written in
    each of CHART_BINS equal steps of time, on the run's own UTC axis. Points are in the chart's own
    1000 x 200 drawing units. Counts and times only; nothing of the text."""
    by_run: dict[str, list[PlantRecord]] = defaultdict(list)
    for r in records:
        by_run[r.run].append(r)
    charts = []
    for run, recs in sorted(by_run.items(), key=lambda kv: kv[1][0].time):
        start, end = recs[0].time, max(r.time for r in recs)
        span = max(end - start, 1.0)
        step = span / CHART_BINS
        table = TABLE_AGENTS.get(recs[0].who)  # a jobs, network or scores session: one line
        series: dict[str, list[int]] = {}
        for r in recs:
            name = table or KIND_LINES.get(r.kind, r.kind)
            bins = series.setdefault(name, [0] * CHART_BINS)
            bins[min(max(int((r.time - start) // step), 0), CHART_BINS - 1)] += 1
        order = [table] if table else [n for n in KIND_LINES.values() if n in series]
        order += [n for n in series if n not in order]
        peak = max(max(v) for v in series.values())
        charts.append(
            {
                "run": run,
                "start": datetime.fromtimestamp(start, UTC).strftime("%Y-%m-%d %H:%M:%S"),
                "end": datetime.fromtimestamp(end, UTC).strftime("%Y-%m-%d %H:%M:%S"),
                "axis": [
                    {"x": 40 + 950 * k / 4, "label": _clock(start + span * k / 4)} for k in range(5)
                ],
                "lines": [
                    {
                        "name": name,
                        "count": sum(series[name]),
                        "points": " ".join(
                            f"{40 + 950 * (i + 0.5) / CHART_BINS:.1f},{170 - 150 * v / peak:.1f}"
                            for i, v in enumerate(series[name])
                        ),
                    }
                    for name in order
                ],
                "peak": peak,
                "step": f"{step / 60:.0f} min" if step >= 90 else f"{step:.0f} s",
                "records": len(recs),
                "batch": recs[0].box["batch"],
            }
        )
    return charts
