"""The contract between the static part and a plan (design spec, sections 3.1 and 3.4).

These types are fixed by the static part. A plan imports them and defines none of its own.
The only call a plan may make back into the static part is ``run_llm``; the plan runtime
or the dry runner installs the real implementation before any plan function runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

RecordKind = Literal["request", "response", "tool_call", "tool_result"]


@dataclass(frozen=True)
class LogRecord:
    """One verified log record as admitted at ingress (design spec, section 2.3)."""

    stream: str
    batch: int
    offset: int
    agent_id: str
    session_id: str
    ts: str
    kind: RecordKind
    content: str


@dataclass(frozen=True)
class QueryInput:
    """A group of records classified together: the multi-log query."""

    records: tuple[LogRecord, ...]
    agent_id: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class ClassifierResult:
    """Parsed by the runtime from the model's strict JSON output; the score is uncalibrated."""

    score: float
    label: str


@dataclass(frozen=True)
class Verdict:
    flagged: bool
    n_queries: int
    n_flagged: int
    max_score: float


@dataclass(frozen=True)
class FilterRules:
    """Empty ``keywords`` keeps every record; ``agents`` of None keeps every agent."""

    keywords: tuple[str, ...] = ()
    kinds: tuple[RecordKind, ...] = ("request", "response", "tool_call", "tool_result")
    agents: tuple[str, ...] | None = None


class RunLLM(Protocol):
    def __call__(self, prompt_id: str, item: QueryInput) -> ClassifierResult: ...


_run_llm: RunLLM | None = None


def install_run_llm(implementation: RunLLM | None) -> None:
    """Called by the plan runtime or the dry runner, never by a plan."""
    global _run_llm
    _run_llm = implementation


def run_llm(prompt_id: str, item: QueryInput) -> ClassifierResult:
    """The one model capability a plan may use.

    The runtime enforces the model, the prompt and the call budget.
    """
    if _run_llm is None:
        raise RuntimeError("run_llm is only available inside the plan runtime or the dry runner")
    return _run_llm(prompt_id, item)
