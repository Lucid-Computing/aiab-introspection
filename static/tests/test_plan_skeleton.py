"""A plan folder follows the aiab.plan_api contract: well-formed plan.json, allowed imports,
run_llm only inside a runtime. Checked on the frozen demo plan under tests/fixtures/demo-plan.
What a plan decides is the parties' business and is not tested here."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from aiab import plan_api
from aiab.plan_api import ClassifierResult, QueryInput

PLAN_DIR = Path(__file__).resolve().parent / "fixtures" / "demo-plan"


@pytest.fixture(scope="module")
def plan():
    spec = importlib.util.spec_from_file_location("demo_plan", PLAN_DIR / "plan.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["demo_plan"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def plan_cfg() -> dict:
    return json.loads((PLAN_DIR / "plan.json").read_text())


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


def test_plan_json_is_well_formed(plan_cfg: dict) -> None:
    assert plan_cfg["schema"] == "aiab-baked-plan/v1"
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


def test_run_llm_is_unavailable_outside_a_runtime(plan) -> None:
    plan_api.install_run_llm(None)
    item = QueryInput(records=(), agent_id="agent-a", session_id="s-0001")
    with pytest.raises(RuntimeError):
        plan.classify(item, model_id="x", prompt_id="bio_research_v1")
