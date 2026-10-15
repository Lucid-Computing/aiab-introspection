"""The demo plan under plan/ follows the aiab.plan_api contract and runs with a fake model."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

from aiab import plan_api
from aiab.plan_api import ClassifierResult, FilterRules, LogRecord, QueryInput

REPO = Path(__file__).resolve().parents[2]
PLAN_DIR = REPO / "plan"


@pytest.fixture(scope="module")
def plan():
    sys.path.insert(0, str(REPO))
    try:
        return importlib.import_module("plan.plan")
    finally:
        sys.path.remove(str(REPO))


@pytest.fixture(scope="module")
def plan_cfg() -> dict:
    return json.loads((PLAN_DIR / "plan.json").read_text())


def load_records(name: str) -> list[LogRecord]:
    lines = (PLAN_DIR / "tests" / "transcripts" / name).read_text().splitlines()
    return [LogRecord(**json.loads(line)) for line in lines if line.strip()]


def fake_run_llm(prompt_id: str, item: QueryInput) -> ClassifierResult:
    """A stand-in for the model: scores a window by whether it mentions laboratory work."""
    assert prompt_id == "bio_research_v1"
    text = " ".join(record.content.lower() for record in item.records)
    lab_terms = ("culture", "primer", "protocol", "iptg", "od600", "assay", "soluble")
    hits = sum(term in text for term in lab_terms)
    return ClassifierResult(score=min(1.0, 0.3 * hits), label="bio" if hits else "benign")


@pytest.fixture(autouse=True)
def install_fake_model():
    plan_api.install_run_llm(fake_run_llm)
    yield
    plan_api.install_run_llm(None)


def run_plan(plan, cfg: dict, records: list[LogRecord]) -> tuple[dict, list[QueryInput]]:
    rules = FilterRules(
        keywords=tuple(cfg["filter"]["keywords"]), kinds=tuple(cfg["filter"]["kinds"])
    )
    kept = plan.filter_logs(records, rules=rules)
    queries = plan.build_query(kept, window=cfg["window"])
    classification = cfg["classification"]
    scores = [
        plan.classify(q, model_id=cfg["model"]["id"], prompt_id=classification["prompt_id"])
        for q in queries
    ]
    verdict = plan.metric(scores, threshold=classification["threshold"])
    return plan.emit_report(verdict), queries


def test_plan_json_is_well_formed(plan_cfg: dict) -> None:
    assert plan_cfg["schema"] == "aiab-plan/v1"
    assert (PLAN_DIR / "prompts" / f"{plan_cfg['classification']['prompt_id']}.txt").is_file()
    assert plan_cfg["window"] <= plan_cfg["limits"]["max_logs_per_query"]
    assert plan_cfg["output_schema"]["additionalProperties"] is False


def test_plan_only_imports_the_allowed_modules() -> None:
    source = (PLAN_DIR / "plan.py").read_text()
    imports = [
        line.strip()
        for line in source.splitlines()
        if line.startswith(("import ", "from ")) and "__future__" not in line
    ]
    for line in imports:
        module = line.split()[1]
        assert module.split(".")[0] in {"dataclasses", "typing", "aiab"}, line
        if module.startswith("aiab"):
            assert module == "aiab.plan_api", line


@pytest.mark.parametrize(
    "transcript", sorted(p.name for p in (PLAN_DIR / "tests" / "transcripts").glob("*.jsonl"))
)
def test_demo_plan_matches_expected_verdicts(plan, plan_cfg: dict, transcript: str) -> None:
    expected = json.loads((PLAN_DIR / "tests" / "expected.json").read_text())[transcript]
    report, queries = run_plan(plan, plan_cfg, load_records(transcript))
    assert set(report) == set(plan_cfg["output_schema"]["required"])
    assert report == {"flagged": expected["flagged"]}
    assert all(len(q.records) <= plan_cfg["window"] for q in queries)
    assert {q.session_id for q in queries} == set(expected["sessions"])


def test_run_llm_is_unavailable_outside_a_runtime(plan) -> None:
    plan_api.install_run_llm(None)
    item = QueryInput(records=(), agent_id="agent-a", session_id="s-0001")
    with pytest.raises(RuntimeError):
        plan.classify(item, model_id="x", prompt_id="bio_research_v1")
