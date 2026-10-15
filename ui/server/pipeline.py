"""The plan graph the UI draws: the six functions in the order the worker calls them.

This mirrors aiab/baked/worker.py (design spec 3.1, 3.3). The static part fixes the
function names and signatures; this table only adds what each step takes from plan.json.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PARTIES: tuple[str, ...] = ("lab", "auditor")


@dataclass(frozen=True)
class Step:
    name: str
    kind: Literal["run_python", "run_llm"]
    input_type: str
    output_type: str
    settings: tuple[str, ...]  # plan.json fields this step takes, dotted
    summary: str
    called: bool = True


PIPELINE: tuple[Step, ...] = (
    Step(
        "filter_logs",
        "run_python",
        "list[LogRecord]",
        "list[LogRecord]",
        ("filter", "input_scope.agents"),
        "Which records are in scope.",
    ),
    Step(
        "build_query",
        "run_python",
        "list[LogRecord]",
        "list[QueryInput]",
        ("window", "limits.max_logs_per_query"),
        "Group records into multi-log classifier inputs.",
    ),
    Step(
        "classify",
        "run_llm",
        "QueryInput",
        "float",
        ("model.id", "classification.prompt_id"),
        "Score one input with the pinned model and prompt, through run_llm.",
    ),
    Step(
        "metric",
        "run_python",
        "list[float]",
        "Verdict",
        ("classification.threshold",),
        "Turn scores into a verdict.",
    ),
    Step(
        "emit_report",
        "run_python",
        "Verdict",
        "dict",
        ("output_schema",),
        "Typed answer that must match output_schema.",
    ),
    Step(
        "appeal",
        "run_python",
        "flag_id",
        "Verdict",
        (),
        "Must exist; never called in the demo (F-9).",
        called=False,
    ),
)
