"""The plan graph the UI draws: the functions in the order the worker calls them.

This mirrors aiab/baked/worker.py (design spec 3.1, 3.3). The static part fixes the
function names and signatures; this table adds what each step takes from plan.json and a
plain-words explanation for the parties.
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
    explain: str = ""  # paragraphs separated by blank lines


PIPELINE: tuple[Step, ...] = (
    Step(
        "build_query",
        "run_python",
        "list[LogRecord]",
        "list[QueryInput]",
        (
            "window",
            "limits.max_logs_per_query",
            "limits.max_records_per_run",
            "limits.max_input_utf8_bytes",
        ),
        "Group records into multi-log classifier inputs.",
        explain=(
            "Groups the kept records into the pieces the model will read, one piece per "
            "model call. Records are grouped by agent and session, and each session is cut "
            "into windows of at most `window` records, so the model sees several related "
            "records together. This is the multi-log query, the reason the audit runs in a "
            "confidential box rather than as a plain per-message filter.\n\n"
            "window: how many records go into one piece. limits.max_logs_per_query: the "
            "most the box allows; a larger window is refused before any model call. "
            "limits.max_records_per_run and max_input_utf8_bytes: how much input one run "
            "may contain at all."
        ),
    ),
    Step(
        "classify",
        "run_llm",
        "QueryInput",
        "float",
        (
            "classification.problem",
            "classification.positive_examples",
            "classification.negative_examples",
            "classification.prompt_id",
            "model.id",
            "model.max_output_tokens",
            "limits.max_model_calls_per_run",
        ),
        "Score one input with the pinned model and prompt, through run_llm.",
        explain=(
            "Asks the model one question about one piece of records and gets back a score "
            "from 0 to 1.\n\n"
            "classification.problem is the question; positive_examples and negative_examples "
            "draw the line between what should and should not be flagged. The prompt file "
            "(classification.prompt_id) is the exact wording the model sees around them.\n\n"
            "The code cannot reach the model itself. It calls run_llm, and the box makes "
            "the call with the pinned model and the agreed prompt, counting it against "
            "limits.max_model_calls_per_run. A different model or prompt is refused."
        ),
    ),
    Step(
        "metric",
        "run_python",
        "list[float]",
        "Verdict",
        ("classification.threshold",),
        "Turn scores into a verdict.",
        explain=(
            "Turns all the scores into one verdict. The demo rule: the run is flagged if any "
            "piece scored above classification.threshold. It also records how many pieces "
            "there were, how many scored above the threshold, and the highest score, so "
            "the rule can be changed to a fraction or a count without touching other steps."
        ),
    ),
    Step(
        "emit_report",
        "run_python",
        "Verdict",
        "dict",
        ("output_schema", "limits.max_result_bytes", "limits.max_runtime_seconds_per_run"),
        "Typed answer that must match output_schema.",
        explain=(
            "Produces the only thing that leaves the box: the typed answer. It must match "
            "output_schema exactly, here a single true or false called flagged. The box's "
            "egress gate rejects anything else, so no raw text, scores or record content "
            "can leak out in a report."
        ),
    ),
    Step(
        "appeal",
        "run_python",
        "flag_id",
        "Verdict",
        (),
        "Must exist; never called in the demo (F-9).",
        called=False,
        explain="Reserved for appeals after the demo. The box never calls it today.",
    ),
)
