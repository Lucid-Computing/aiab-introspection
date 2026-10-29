"""The plan format: what the loader admits, what it refuses and why, and what it derives.

Checked on the frozen graph plan under tests/fixtures/graph-plan. The rules here mirror
the builder's (ui/playground/src/plan); a plan the builder shows as valid loads here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aiab.audit.protocol import clone, decode
from aiab.baked import graph
from aiab.baked.output import capacity, check_schema
from aiab.baked.plan_ir import PlanError, check_code, check_plan, report_schema

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "graph-plan"


@pytest.fixture
def plan():
    return decode((FIXTURE / "plan.json").read_bytes())


@pytest.fixture
def trust_model():
    return decode((FIXTURE / "trust.json").read_bytes())["model"]


def node(plan, node_id):
    return next(n for n in plan["nodes"] if n["id"] == node_id)


def issues_of(plan, trust_model=None):
    with pytest.raises(PlanError) as failure:
        check_plan(plan, trust_model)
    return failure.value.issues


def test_fixture_plan_loads_and_derives_the_runtime_settings(plan, trust_model):
    analysis = check_plan(plan, trust_model)
    assert analysis.order == ("logs", "commands", "effects", "destructive", "report")
    assert analysis.limits == {
        "max_input_utf8_bytes": 200000,
        "max_model_calls_per_run": 128,
        "max_runtime_seconds_per_run": 240,
        "max_records_per_run": 2000,
        "max_result_bytes": 1024,
    }
    assert analysis.input_scope is None  # no window and no agents pinned: chosen per run
    schema = analysis.output_schema
    assert schema["required"] == ["flagged", "destructive_sessions", "sessions"]
    assert schema["additionalProperties"] is False
    assert check_schema(schema) == 1 + 33 + 33  # a boolean and two 2**32-wide integers
    assert capacity(analysis.config) == 67


def test_every_issue_names_its_node_and_path(plan):
    bad = clone(plan)
    node(bad, "destructive")["config"]["labels"].append(
        {"name": "safe", "description": "", "flag": False}
    )
    issues = issues_of(bad)
    assert issues == [
        {
            "message": 'destructive: label "safe" appears twice',
            "node": "destructive",
            "path": ["nodes", 3, "config", "labels"],
        }
    ]


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        (lambda p: p.pop("limits"), "'limits' is a required property"),
        (lambda p: p.__setitem__("version", "aiab-plan-ir/v1"), "version"),
        (
            lambda p: node(p, "logs")["config"].__setitem__(
                "filter", {"kinds": [], "keywords": []}
            ),
            "kinds",
        ),
        (
            lambda p: node(p, "report")["inputs"].__setitem__("result", "logs.records"),
            "receives LogRecords",
        ),
        (
            lambda p: node(p, "destructive")["inputs"].__setitem__("items", "logs.nothing"),
            'no output "nothing"',
        ),
        (
            lambda p: node(p, "destructive")["inputs"].__setitem__("items", "missing.results"),
            'no node "missing"',
        ),
        (
            lambda p: node(p, "destructive")["inputs"].__setitem__("extra", "logs.records"),
            'unknown input "extra"',
        ),
        (
            lambda p: node(p, "logs")["inputs"].__setitem__("x", "commands.sessions"),
            "takes no inputs",
        ),
        (
            lambda p: node(p, "commands")["inputs"].__setitem__("records", "destructive.results"),
            "Cycle",
        ),
        (lambda p: node(p, "effects")["config"].__setitem__("user_prompt", "{{nope}}"), "{{nope}}"),
        (
            lambda p: node(p, "effects")["config"]["output"].__setitem__("type", "LogRecords"),
            "Items (one answer",
        ),
        (lambda p: node(p, "destructive")["config"]["labels"].pop(), "at least two labels"),
        (
            lambda p: node(p, "destructive")["config"]["labels"].append(
                {"name": "abstain", "description": "", "flag": False}
            ),
            "reserved",
        ),
        (
            lambda p: node(p, "report")["config"]["fields"].append(
                {"name": "note", "type": "string"}
            ),
            "allow_free_text",
        ),
        (
            lambda p: node(p, "report")["config"]["fields"][0].__setitem__("from", "any.benign"),
            'unknown label "benign"',
        ),
        (
            lambda p: node(p, "report")["config"]["fields"][2].__setitem__("type", "boolean"),
            "declared boolean but count is integer",
        ),
        (
            lambda p: node(p, "report")["config"]["fields"][0].__setitem__("from", "label"),
            "only as aggregates",
        ),
        (lambda p: p["nodes"].append(clone(node(p, "report"))), 'Duplicate node id "report"'),
        (
            lambda p: node(p, "commands")["config"].__setitem__(
                "code", "def go(records):\n    return []\n"
            ),
            "define run",
        ),
        (
            lambda p: node(p, "commands")["config"].__setitem__(
                "code", "import os\ndef run(records):\n    return []\n"
            ),
            "import os is not allowed",
        ),
    ],
)
def test_refused_plans_say_what_is_wrong(plan, change, fragment):
    bad = clone(plan)
    change(bad)
    assert any(fragment in i["message"] for i in issues_of(bad)), issues_of(bad)


def test_model_pins_are_checked_against_the_trust_file(plan, trust_model):
    check_plan(plan, trust_model)
    bad = clone(plan)
    node(bad, "effects")["config"]["model"]["id"] = "claude-opus-5-5"
    assert "not the pinned model" in issues_of(bad, trust_model)[0]["message"]
    bad = clone(plan)
    node(bad, "destructive")["config"]["model"]["temperature"] = 0.7
    assert "temperature 0" in issues_of(bad, trust_model)[0]["message"]


def test_value_report_fields_resolve_against_declared_fields(plan):
    p = clone(plan)
    p["nodes"].insert(
        4,
        {
            "id": "aggregate",
            "type": "run_python",
            "inputs": {"labels": "destructive.results"},
            "config": {
                "code": "def run(labels):\n    return {'flagged': True, 'share': 1.0}\n",
                "output": {
                    "name": "summary",
                    "type": "Value",
                    "fields": {"flagged": "boolean", "share": "number"},
                },
            },
        },
    )
    report = node(p, "report")
    report["inputs"]["result"] = "aggregate.summary"
    report["config"]["fields"] = [
        {"name": "flagged", "type": "boolean"},
        {"name": "share", "type": "number"},
    ]
    analysis = check_plan(p)
    assert analysis.order.index("aggregate") < analysis.order.index("report")
    report["config"]["fields"].append({"name": "sessions", "type": "integer"})
    assert 'declares no field "sessions"' in issues_of(p)[0]["message"]


@pytest.mark.parametrize(
    ("code", "why"),
    [
        ("def run(a, b):\n    return {}\n", "does not match the inputs"),
        ("def run(**inputs):\n    return {}\n", None),
        ("def run(records):\n    return open('/etc/passwd')\n", "open is not allowed"),
        ("def run(records):\n    return records.__class__\n", "__class__"),
        ("def run(records):\n    return getattr(records, 'x')\n", "getattr is not allowed"),
        ("from re import _compile\ndef run(records):\n    return []\n", "private names"),
        ("import re\nPAT = re.compile('x')\ndef run(records):\n    return []\n", None),
        ("class X: pass\ndef run(records):\n    return []\n", "module level"),
        ("print(1)\ndef run(records):\n    return []\n", "module level"),
        ("def run(records=None):\n    return []\n", "plain named parameters"),
    ],
)
def test_run_python_code_is_admitted_or_refused(code, why):
    verdict = check_code(code, ("records",))
    if why is None:
        assert verdict is None
    else:
        assert why in verdict


def test_report_schema_is_the_bounded_egress_form(plan):
    report = node(plan, "report")
    report["config"]["fields"].append({"name": "rate", "type": "number"})
    schema = report_schema(report)
    assert schema["properties"]["rate"] == {"type": "number"}
    assert schema["properties"]["sessions"]["minimum"] == -(2**31)
    assert check_schema(schema) == 1 + 33 + 33 + 64


# --------------------------------------------------------------------------- the engine


def records_of(name):
    return [
        json.loads(line)
        for line in (FIXTURE / "tests/transcripts" / name).read_text().splitlines()
        if line.strip()
    ]


def scripted(answers):
    """An llm callback that answers each node from a table and records what it was asked."""
    asked = {}

    def llm(node_id, texts):
        asked[node_id] = texts
        reply = answers[node_id]
        return [reply(t) for t in texts] if callable(reply) else reply

    llm.asked = asked
    return llm


def test_engine_runs_the_graph_and_aggregates_the_report(plan):
    analysis = check_plan(plan)
    llm = scripted(
        {
            "effects": lambda text: {
                "text": "rm -rf ~/projects/legacy-service deletes another project"
            },
            "destructive": lambda text: {"label": "destructive", "score": 0.93},
        }
    )
    result = graph.run_plan(
        plan, list(analysis.order), records_of("destructive_cleanup.jsonl"), llm
    )
    assert result == {"flagged": True, "destructive_sessions": 1, "sessions": 1}
    # The Python step kept the one session with tool calls, in order, as one item.
    (session_text,) = llm.asked["effects"]
    assert session_text.startswith("Session:\nuser: The integration test")
    assert "tool call: Bash" in session_text and "rm -rf ~/projects/legacy-service" in session_text
    assert llm.asked["destructive"] == ["rm -rf ~/projects/legacy-service deletes another project"]


def test_threshold_turns_low_scores_into_abstain_and_counts_follow(plan):
    analysis = check_plan(plan)
    llm = scripted(
        {
            "effects": lambda text: {"text": "No destructive commands."},
            "destructive": lambda text: {"label": "destructive", "score": 0.2},  # below 0.6
        }
    )
    result = graph.run_plan(plan, list(analysis.order), records_of("mixed_sessions.jsonl"), llm)
    assert result == {"flagged": False, "destructive_sessions": 0, "sessions": 2}


def test_min_count_suppresses_small_counts(plan):
    p = clone(plan)
    node(p, "report")["config"]["privacy"]["min_count"] = 3
    analysis = check_plan(p)
    llm = scripted(
        {
            "effects": lambda text: {"text": "x"},
            "destructive": lambda text: {"label": "destructive", "score": 1.0},
        }
    )
    result = graph.run_plan(p, list(analysis.order), records_of("mixed_sessions.jsonl"), llm)
    assert result == {"flagged": True, "destructive_sessions": 0, "sessions": 0}


def test_python_output_must_have_the_declared_shape(plan):
    p = clone(plan)
    node(p, "commands")["config"]["code"] = (
        "def run(records):\n    return [{'id': 's', 'text': 1}]\n"
    )
    analysis = check_plan(p)
    with pytest.raises(graph.StepError, match="each item needs"):
        graph.run_plan(p, list(analysis.order), records_of("benign_feature.jsonl"), scripted({}))


def test_records_reach_python_with_ids_and_only_selected_kinds(plan):
    p = clone(plan)
    node(p, "commands")["config"]["code"] = (
        "def run(records):\n"
        "    kinds = sorted({r['kind'] for r in records})\n"
        "    first = records[0]\n"
        "    return [{'id': first['id'], 'text': ','.join(kinds), 'record_ids': [first['id']]}]\n"
    )
    analysis = check_plan(p)
    llm = scripted(
        {"effects": lambda t: {"text": t}, "destructive": lambda t: {"label": "safe", "score": 0.9}}
    )
    graph.run_plan(p, list(analysis.order), records_of("benign_feature.jsonl"), llm)
    assert llm.asked["effects"] == ["Session:\nrequest,tool_call\n"]


def test_classifier_groups_log_records_by_session_in_windows(plan):
    p = clone(plan)
    # Classify the records directly, two per call, instead of the summaries.
    node(p, "destructive")["inputs"]["items"] = "logs.records"
    node(p, "destructive")["config"]["window"] = 2
    p["nodes"] = [n for n in p["nodes"] if n["id"] not in ("commands", "effects")]
    analysis = check_plan(p)
    llm = scripted({"destructive": lambda t: {"label": "safe", "score": 0.9}})
    result = graph.run_plan(p, list(analysis.order), records_of("mixed_sessions.jsonl"), llm)
    texts = llm.asked["destructive"]
    assert all(t.count("\n") <= 1 for t in texts)  # at most two records per item
    assert result["sessions"] == len(texts) > 2  # one Classification per window
