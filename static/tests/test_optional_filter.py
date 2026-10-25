"""Plans may omit the filter block and the filter_logs step; input is chosen at run time."""

import json
from pathlib import Path

import pytest

from aiab.audit.protocol import AuditError, validate
from aiab.baked.bundle import PLAN, check_source

PLAN_DIR = Path(__file__).resolve().parent / "fixtures" / "demo-plan"  # frozen demo plan


def test_schema_accepts_a_plan_without_filter():
    cfg = json.loads((PLAN_DIR / "plan.json").read_text())
    cfg.pop("filter")
    validate(cfg, PLAN)  # no AuditError


def test_schema_still_accepts_the_committed_plan():
    validate(json.loads((PLAN_DIR / "plan.json").read_text()), PLAN)


def test_source_without_filter_logs_passes_and_other_omissions_fail():
    source = (PLAN_DIR / "plan.py").read_text()
    without = _drop_function(source, "filter_logs")
    check_source(without)
    with pytest.raises(AuditError):
        check_source(_drop_function(source, "metric"))


def _drop_function(source: str, name: str) -> str:
    import ast

    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return "".join(lines[: node.lineno - 1]) + "".join(lines[node.end_lineno :])
    raise AssertionError(name)


def test_schema_accepts_a_plan_without_input_scope():
    cfg = json.loads((PLAN_DIR / "plan.json").read_text())
    cfg.pop("input_scope")
    validate(cfg, PLAN)  # no AuditError
