"""Runtime qualification with explicit model doubles and narrow evidence fixtures.

These test real Pi + kernel sandboxes, but do not claim live model inference.
"""

import asyncio
import copy
import json
import sys
import types

import pytest

from aiab.workbench.model import (
    MODEL_ID,
    MODEL_PROFILE,
    DeepSeekModel,
    pi_completion,
    validate_completion,
    wire_messages,
)
from aiab.workbench.runner import (
    authorized_dataset,
    budgets,
    execute_plan,
    graph_order,
    validate_findings,
)
from aiab.workbench.tools import RuntimeFailure, ToolBroker, isolated_python


class Store:
    def __init__(self, dataset=None, scope=None):
        self.events = copy.deepcopy((dataset or DATASET)["events"])

    def coverage(self):
        return {"integrity": "synthetic", "complete": False}

    def search(self, query, **filters):
        return [e for e in self.events if query.lower() in e["content"].lower()]

    def query_sql(self, sql):
        raise ValueError("use real evidence SQL integration test")


EVENTS = [
    {
        "id": "e1",
        "actor": "alice",
        "content": "Action outside scope in session one",
        "session_id": "one",
        "campaign_id": "camp",
        "occurred_at": "2026-10-01T00:00:00Z",
    },
    {
        "id": "e2",
        "actor": "alice",
        "content": "Policy update in session two permits action",
        "session_id": "two",
        "campaign_id": "camp",
        "occurred_at": "2026-10-01T00:00:20Z",
    },
    {"id": "private", "actor": "bob", "content": "Do not expose another subject"},
]
DATASET = {"id": "synthetic", "events": EVENTS, "manifest": {}}
PLAN = {
    "version": "aiab-workbench/v1",
    "id": "test",
    "question": "Was action authorized?",
    "mode": "personal",
    "criteria": [{"id": "scope", "description": "effective scope"}],
    "scope": {"dataset_ids": ["synthetic"], "subjects": []},
    "steps": [{"id": "agent", "kind": "agent", "inputs": [], "config": {}}],
    "agent": {"provider": "tinfoil", "model": MODEL_ID, "max_seconds": 15},
}


def finding(**kwargs):
    return {
        "id": "f1",
        "criterion_id": "scope",
        "title": "Effective scope",
        "outcome": "insufficient_evidence",
        "claim": "Need policy chronology",
        "evidence_ids": ["e1"],
        "counterevidence_ids": [],
        "limitations": ["synthetic"],
        "method": "agent",
        "details": {},
        **kwargs,
    }


def call(name, arguments, cid="c1"):
    return {
        "id": cid,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps({"request": json.dumps(arguments)})},
    }


class Model:
    is_test_model = True

    def __init__(self, messages=None, delay=0):
        self.requests, self.closed, self.delay = [], False, delay
        self.entered = asyncio.Event()
        self.messages = messages or [
            {"tool_calls": [call("search", {"query": "scope"})]},
            {"tool_calls": [call("fetch", {"ids": ["e1", "e2"]})]},
            {"tool_calls": [call("counterevidence", {"query": "permits"})]},
            {"content": json.dumps({"findings": [finding(counterevidence_ids=["e2"])]})},
        ]

    async def start(self):
        pass

    async def count_tokens(self, messages, tools):
        return 100

    async def complete(self, messages, tools, max_tokens):
        self.entered.set()
        await asyncio.sleep(self.delay)
        self.requests.append(copy.deepcopy(messages))
        message = self.messages[len(self.requests) - 1]
        return {
            "message": message,
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        }

    async def close(self):
        self.closed = True


@pytest.fixture
def narrow_contracts(monkeypatch):
    """Only runner unit tests use these fixtures; no production fallback modules."""
    contracts = types.ModuleType("aiab.workbench.contracts")
    contracts.validate_plan = lambda p: p
    contracts.validate_dataset = lambda d: d
    contracts.plan_digest = lambda p: "explicit-test-fixture-plan-digest"
    evidence = types.ModuleType("aiab.workbench.evidence")
    evidence.EvidenceStore = Store
    monkeypatch.setitem(sys.modules, contracts.__name__, contracts)
    monkeypatch.setitem(sys.modules, evidence.__name__, evidence)


async def test_malformed_search_can_be_corrected_within_budget(narrow_contracts):
    model = Model()
    model.messages.insert(0, {"tool_calls": [call("search", {"query": ["invalid"]})]})
    result = await execute_plan(PLAN, DATASET, model=model, actor="alice")
    assert result["usage"]["turns"] == 5
    assert result["usage"]["tool_calls"] == 4
    assert "invalid_search" in json.dumps(model.requests[1])
    assert result["findings"][0]["counterevidence_ids"] == ["e2"]


async def test_pi_real_multiturn_with_test_model(narrow_contracts):
    model = Model()
    result = await execute_plan(PLAN, DATASET, actor="alice", model=model)
    assert result["status"] == "completed"
    assert result["usage"]["test_model"] is True
    assert result["usage"]["turns"] == 4
    assert result["usage"]["tool_calls"] == 3
    assert result["usage"]["total_tokens"] == 480
    assert result["coverage"]["examined_ids"] == ["e1", "e2"]
    assert all("private" not in json.dumps(m) for m in model.requests)
    assert all(e["private"] is True for e in result["trace"])
    assert result["trace_commitment"] == result["trace"][-1]["digest"]
    assert model.closed
    assert model.requests[-1][-1]["role"] == "tool"


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("max_turns", 1, "turn_budget_exhausted"),
        ("max_tool_calls", 1, "tool_budget_exhausted"),
        ("max_tokens", 100, "token_budget_exhausted"),
    ],
)
async def test_external_budgets(narrow_contracts, field, value, code):
    plan = copy.deepcopy(PLAN)
    plan["agent"][field] = value
    with pytest.raises(RuntimeFailure, match=code):
        await execute_plan(plan, DATASET, actor="alice", model=Model())


async def test_external_deadline(narrow_contracts):
    plan = copy.deepcopy(PLAN)
    plan["agent"]["max_seconds"] = 1
    m = Model(delay=20)
    with pytest.raises(RuntimeFailure, match="runtime_deadline_exceeded"):
        await execute_plan(plan, DATASET, actor="alice", model=m)
    assert m.closed


async def test_cancellation_kills_child(narrow_contracts, monkeypatch):
    import aiab.workbench.runner as runtime

    killed = []
    original = runtime.kill_process

    async def kill(p):
        await original(p)
        killed.append(p.returncode)

    monkeypatch.setattr(runtime, "kill_process", kill)
    m = Model(delay=30)
    task = asyncio.create_task(execute_plan(PLAN, DATASET, actor="alice", model=m))
    await asyncio.wait_for(m.entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert killed and killed[0] is not None and m.closed


async def test_forbidden_tool_and_unfetched_reference(narrow_contracts):
    with pytest.raises(RuntimeFailure, match="forbidden_tool"):
        await execute_plan(
            PLAN,
            DATASET,
            actor="alice",
            model=Model([{"tool_calls": [call("bash", {"command": "id"})]}]),
        )
    with pytest.raises(RuntimeFailure, match="unexamined_evidence_reference"):
        await execute_plan(
            PLAN,
            DATASET,
            actor="alice",
            model=Model([{"content": json.dumps({"findings": [finding()]})}]),
        )


async def test_python_seccomp_computation_and_escape_denial():
    assert await isolated_python("result = sum(range(100))", []) == 4950
    for code in [
        "result = open('/etc/passwd').read()",
        "import socket; result = socket.socket().fileno()",
        "import os; os.fork()",
        "while True: pass",
        "print('x'*300000); result = 1",
    ]:
        with pytest.raises(RuntimeFailure, match="python_failed"):
            await isolated_python(code, [])


async def test_join_and_fetch_capabilities():
    broker = ToolBroker(Store({"events": EVENTS[:2]}))
    result = await broker.call(
        "join", {"left_ids": ["e1"], "right_ids": ["e2"], "on": "campaign_id", "within_seconds": 30}
    )
    assert result["rows"] == [{"left_id": "e1", "right_id": "e2"}]
    assert (
        await broker.call(
            "join",
            {"left_ids": ["e1"], "right_ids": ["e2"], "on": "campaign_id", "within_seconds": 10},
        )
    )["rows"] == []
    with pytest.raises(RuntimeFailure, match="unauthorized_evidence"):
        await broker.call("fetch", {"ids": ["private"]})
    with pytest.raises(RuntimeFailure, match="forbidden_tool"):
        await broker.call("http", {"url": "https://example.com"})


def test_authorization_and_plan_graph():
    with pytest.raises(RuntimeFailure, match="authenticated_actor"):
        authorized_dataset(PLAN, DATASET, None)
    data, scope = authorized_dataset(PLAN, DATASET, "alice")
    assert len(data["events"]) == 2 and scope["subjects"] == ["alice"]
    plan = copy.deepcopy(PLAN)
    plan["scope"]["subjects"] = ["bob"]
    with pytest.raises(RuntimeFailure, match="subject_outside"):
        authorized_dataset(plan, DATASET, "alice")
    plan["mode"] = "confidential"
    with pytest.raises(RuntimeFailure, match="dataset_capability"):
        authorized_dataset(plan, DATASET, "alice")
    with pytest.raises(RuntimeFailure, match="invalid_step_graph"):
        graph_order([{"id": "a", "inputs": ["b"]}, {"id": "b", "inputs": ["a"]}])


def test_reference_schema_and_spans():
    broker = ToolBroker(Store())
    for f in [
        finding(criterion_id="invented"),
        finding(evidence_ids=["unknown"]),
        finding(outcome="supported", evidence_ids=[]),
        finding(details={"spans": [{"event_id": "e1", "start": 0, "end": 3, "text": "bad"}]}),
    ]:
        with pytest.raises(RuntimeFailure):
            validate_findings([f], PLAN, broker)


def test_no_provider_or_release_fallback():
    with pytest.raises(RuntimeFailure, match="unapproved_model_binding"):
        DeepSeekModel(profile={**MODEL_PROFILE, "artifact_digest": "pretend"})
    with pytest.raises(RuntimeFailure, match="unapproved_model"):
        budgets({"agent": {"provider": "openai"}})
    with pytest.raises(RuntimeFailure, match="missing_reviewed"):
        DeepSeekModel(host="api.openai.com", credential="test")


def test_deepseek_reasoning_tool_roundtrip():
    answer = {
        "model": MODEL_ID,
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "private",
                    "tool_calls": [call("coverage", {})],
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }
    pi = pi_completion(validate_completion(answer))
    messages = wire_messages([{"role": "assistant", **pi}], "fixed")
    assert messages[-1]["reasoning"] == "private"
    assert messages[-1]["tool_calls"][0]["function"]["name"] == "coverage"
    answer["model"] = "some-other-model"
    with pytest.raises(RuntimeFailure, match="invalid_deepseek"):
        validate_completion(answer)


async def test_rules_dispatch_uses_rule_not_fixture_labels(narrow_contracts, monkeypatch):
    calls = []
    module = types.ModuleType("aiab.workbench.analyzers")

    def evaluate(rule_id, events, config):
        calls.append((rule_id, events, config))
        return [finding()]

    async def isolated(operation, events, **parameters):
        assert operation == "rules"
        return evaluate(parameters["rule"], events, parameters["config"])

    monkeypatch.setattr("aiab.workbench.runner.isolated_operation", isolated)
    module.evaluate = evaluate
    monkeypatch.setitem(sys.modules, module.__name__, module)
    plan = copy.deepcopy(PLAN)
    plan["steps"] = [
        {"id": "rules", "kind": "rules", "inputs": [], "config": {"rule": "scope_check"}}
    ]
    result = await execute_plan(plan, DATASET, actor="alice")
    assert calls[0][0] == "scope_check" and len(calls[0][1]) == 2
    assert result["usage"]["turns"] == 0 and result["findings"][0]["id"] == "f1"


async def test_sql_read_only_bounded_and_explicit_inputs():
    from aiab.workbench.tools import isolated_operation

    result = await isolated_operation(
        "sql", EVENTS[:2], sql="SELECT campaign_id, count(*) FROM events GROUP BY campaign_id"
    )
    assert result == {"columns": ["campaign_id", "count(*)"], "rows": [["camp", 2]]}
    for sql in [
        "ATTACH DATABASE '/tmp/aiab-escape' AS extra",
        "DELETE FROM events",
        "SELECT load_extension('anything')",
        "SELECT * FROM sqlite_master",
        "WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM t) SELECT max(n) FROM t",
    ]:
        with pytest.raises(RuntimeFailure, match="sql_failed"):
            await isolated_operation("sql", EVENTS[:2], sql=sql)


async def test_agent_step_cannot_widen_selected_graph_inputs(narrow_contracts):
    plan = copy.deepcopy(PLAN)
    plan["steps"] = [
        {"id": "select", "kind": "select", "inputs": [], "config": {"ids": ["e1"]}},
        {"id": "agent", "kind": "agent", "inputs": ["select"], "config": {}},
    ]
    with pytest.raises(RuntimeFailure, match="unauthorized_evidence_reference"):
        await execute_plan(
            plan,
            DATASET,
            actor="alice",
            model=Model([{"tool_calls": [call("fetch", {"ids": ["e2"]})]}]),
        )


async def test_deterministic_graph_has_no_inference_and_release_is_only_barrier(narrow_contracts):
    plan = copy.deepcopy(PLAN)
    plan["steps"] = [
        {"id": "select", "kind": "select", "inputs": [], "config": {"ids": ["e1", "e2"]}},
        {
            "id": "sql",
            "kind": "sql",
            "inputs": ["select"],
            "config": {"sql": "SELECT count(*) FROM events"},
        },
        {
            "id": "python",
            "kind": "python",
            "inputs": ["select"],
            "config": {"code": "result = len(events)"},
        },
        {
            "id": "aggregate",
            "kind": "aggregate",
            "inputs": ["select"],
            "config": {"group_by": "campaign_id", "metric": "count"},
        },
        {"id": "assert", "kind": "assert", "inputs": ["select"], "config": {"min_events": 2}},
        {
            "id": "release",
            "kind": "release",
            "inputs": ["aggregate", "python", "sql"],
            "config": {},
        },
    ]
    result = await execute_plan(plan, DATASET, actor="alice")
    assert result["usage"]["turns"] == 0 and result["usage"]["test_model"] is False
    assert result["release_state"] == "private_pending_policy"


async def test_live_execution_requires_explicit_signed_release_profile(narrow_contracts):
    with pytest.raises(RuntimeFailure, match="signed_model_profile_required"):
        await execute_plan(PLAN, DATASET, actor="alice")


async def test_provider_cannot_spend_more_than_reserved(narrow_contracts):
    class MisreportingModel(Model):
        async def complete(self, messages, tools, max_tokens):
            return {
                "message": {"content": "{}"},
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": max_tokens + 1,
                    "total_tokens": 100 + max_tokens + 1,
                },
            }

    with pytest.raises(RuntimeFailure, match="provider_usage_exceeds_reservation"):
        await execute_plan(PLAN, DATASET, actor="alice", model=MisreportingModel())


async def test_unaddressed_criteria_and_counterevidence_required(narrow_contracts):
    for messages, code in [
        ([{"content": json.dumps({"findings": []})}], "unaddressed_criteria"),
        (
            [
                {"tool_calls": [call("fetch", {"ids": ["e1"]})]},
                {"content": json.dumps({"findings": [finding(outcome="supported")]})},
            ],
            "counterevidence_search_required",
        ),
    ]:
        with pytest.raises(RuntimeFailure, match=code):
            await execute_plan(PLAN, DATASET, actor="alice", model=Model(messages))


def test_changed_attestation_fails_before_body_transmission():
    from aiab.audit.protocol import AuditError
    from aiab.audit.transport import PinnedConnection

    p = PinnedConnection("test.tinfoil.dev", MODEL_PROFILE["repo"], MODEL_PROFILE["release_digest"])
    doc = types.SimpleNamespace(
        security_verified=True,
        config_repo=MODEL_PROFILE["repo"],
        enclave_host="test.tinfoil.dev",
        release_digest=MODEL_PROFILE["release_digest"].removeprefix("sha256:"),
        steps={
            name: types.SimpleNamespace(status="success")
            for name in ("fetch_digest", "verify_code", "verify_enclave", "compare_measurements")
        },
    )
    p.secure = types.SimpleNamespace(
        get_verification_document=lambda: doc,
        enclave="test.tinfoil.dev",
        _ground_truth=types.SimpleNamespace(public_key="one"),
    )
    p.check()
    p.secure._ground_truth.public_key = "changed"
    with pytest.raises(AuditError):
        p.check()
    assert p.failed
    p.secure._ground_truth.public_key = "one"
    with pytest.raises(AuditError):
        p.check()  # identity changes permanently poison the run's transport


async def test_python_bounded_reader_preserves_valid_large_result():
    value = await isolated_python("result = 'x' * 100000", [])
    assert len(value) == 100000


async def test_all_operator_configs_checked_before_first_model_call(narrow_contracts):
    plan = copy.deepcopy(PLAN)
    plan["steps"].append(
        {
            "id": "bad",
            "kind": "python",
            "inputs": [],
            "config": {"code": "result=1", "ambient_network": True},
        }
    )
    model = Model()
    with pytest.raises(RuntimeFailure, match="invalid_python_config"):
        await execute_plan(plan, DATASET, actor="alice", model=model)
    assert not model.requests


def test_reviewed_deployment_asset_binds_actual_config_and_model_pack():
    import base64
    import hashlib

    from aiab.workbench.tools import RUNTIME

    raw = (RUNTIME / "review/tinfoil-deployment.json").read_bytes()
    assert "sha256:" + hashlib.sha256(raw).hexdigest() == MODEL_PROFILE["release_digest"]
    deployment = json.loads(raw)
    config = base64.b64decode(deployment["config"])
    assert hashlib.sha256(config).hexdigest() in deployment["cmdline"]
    assert MODEL_PROFILE["artifact_digest"].removeprefix("sha256:") in config.decode()
    assert MODEL_PROFILE["serving_image"] in config.decode()
    assert config == (RUNTIME / "review/tinfoil-config.yml").read_bytes()


async def test_tokenize_and_inference_use_same_deepseek_template_and_reasoning():
    requests = []

    class Connection:
        async def request(self, method, path, body, credential, **kwargs):
            requests.append((path, json.loads(body)))
            if path == "/tokenize":
                return 200, {"count": 100}
            return 200, {
                "model": MODEL_ID,
                "choices": [
                    {"finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 2, "total_tokens": 102},
            }

    model = DeepSeekModel(host="test.tinfoil.dev", credential="explicit-test")
    model.connection = Connection()
    history = [{"role": "user", "content": "synthetic"}]
    await model.count_tokens(history, [])
    await model.complete(history, [], 512)
    tokenize, inference = requests[0][1], requests[1][1]
    assert (
        tokenize["chat_template_kwargs"]
        == inference["chat_template_kwargs"]
        == {"enable_thinking": True, "reasoning_effort": "low", "drop_thinking": False}
    )
