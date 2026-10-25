"""Demo plan: bio-research classifier over multi-log queries.

The static runtime calls these functions in a fixed order (design spec, section 3.1):
build_query -> classify (per query) -> metric -> emit_report. Which records come in is
chosen at run time; the box hands every record in scope to build_query.
This module may import only dataclasses, typing and aiab.plan_api. It has no network,
file, clock or randomness access and must be deterministic for the same inputs.
"""

from __future__ import annotations

from typing import Literal

from aiab.plan_api import LogRecord, QueryInput, Verdict, run_llm


def build_query(logs: list[LogRecord], *, window: int) -> list[QueryInput]:
    """Group records by (agent, session) in log order.

    Each session is cut into windows of at most `window` records.
    """
    sessions: dict[tuple[str, str], list[LogRecord]] = {}
    for record in logs:
        sessions.setdefault((record.agent_id, record.session_id), []).append(record)
    queries: list[QueryInput] = []
    for (agent_id, session_id), records in sessions.items():
        ordered = sorted(records, key=lambda r: (r.batch, r.offset))
        for start in range(0, len(ordered), window):
            queries.append(
                QueryInput(
                    records=tuple(ordered[start : start + window]),
                    agent_id=agent_id,
                    session_id=session_id,
                )
            )
    return queries


def classify(item: QueryInput, *, model_id: str, prompt_id: str) -> float:
    """Score in [0, 1].

    The runtime enforces that `model_id` is the plan's model; the plan only picks the prompt.
    """
    return run_llm(prompt_id, item).score


def metric(scores: list[float], *, threshold: float) -> Verdict:
    """Demo rule: flag if any query scores above the threshold."""
    above = [score for score in scores if score > threshold]
    return Verdict(
        flagged=bool(above),
        n_queries=len(scores),
        n_flagged=len(above),
        max_score=max(scores, default=0.0),
    )


def emit_report(verdict: Verdict) -> dict:
    """Must validate against plan.json `output_schema`; the egress gate rejects anything else."""
    return {"flagged": verdict.flagged}


def appeal(flag_id: str, *, kind: Literal["data_correction", "secondary_query"]) -> Verdict:
    """Not called in the demo (design spec, section 3.4)."""
    raise NotImplementedError("Appeals are not part of the demo plan")
