"""Merged Northstar -> contracts/evidence -> sandbox/Pi integration.

Inference doubles are explicit; evidence, domain computations, Pi and OS sandbox
are real. Oracle labels remain solely in the test process.
"""

import asyncio
import copy
import hashlib
import json
import sys

import pytest

from aiab.workbench import datasets
from aiab.workbench.evidence import EvidenceError, EvidenceStore
from aiab.workbench.fixtures.generator import build
from aiab.workbench.fixtures.specs import SPECS
from aiab.workbench.runner import execute_plan
from aiab.workbench.tools import (
    RuntimeFailure,
    ToolBroker,
    isolated_operation,
    kill_process,
)


@pytest.fixture(scope="module")
def northstar():
    return build(17, include_truth=True)


def admission(dataset):
    return {
        "id": "auditor",
        "authorized_dataset_ids": [dataset["id"]],
        "authorized_subjects": sorted({e["actor"] for e in dataset["events"] if e["actor"]}),
    }


def deterministic_plan(rule, dataset):
    plan = datasets.plan_for(rule, dataset=dataset)
    plan["steps"] = plan["steps"][:1]
    return plan


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.id)
async def test_catalog_real_execute_all_methods(northstar, spec):
    dataset, truth = northstar
    result = await execute_plan(
        deterministic_plan(spec.id, dataset), dataset, actor=admission(dataset)
    )
    labels = {
        "finding-"
        + hashlib.sha256(f"{c['audit_id']}:{c['investigation_id']}".encode()).hexdigest()[:20]: c
        for c in truth["cases"]
        if c["audit_id"] == spec.id
    }
    assert len(result["findings"]) == len(labels) == 16
    assert set(f["outcome"] for f in result["findings"]) == {
        "supported",
        "no_finding",
        "insufficient_evidence",
    }
    for f in result["findings"]:
        case = labels[f["id"]]
        assert f["outcome"] == case["expected_supportable"]
        assert set(f["evidence_ids"] + f["counterevidence_ids"]) <= set(case["evidence_ids"])
        if f["outcome"] == "supported":
            assert f["evidence_ids"] and f["details"]
        assert f["limitations"]
    assert result["usage"]["turns"] == 0
    assert result["coverage"]["negative_claims_verified"] is False
    assert "native_completeness_unverified" in result["coverage"]["source_coverage"]["limitations"]


class InferenceDouble:
    is_test_model = True

    def __init__(self, messages, delay=0):
        self.messages, self.delay = messages, delay
        self.requests, self.closed = [], False
        self.entered = asyncio.Event()

    async def start(self):
        pass

    async def count_tokens(self, messages, tools):
        return 100

    async def complete(self, messages, tools, max_tokens):
        self.entered.set()
        await asyncio.sleep(self.delay)
        self.requests.append(copy.deepcopy(messages))
        return {
            "message": self.messages[len(self.requests) - 1],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        }

    async def close(self):
        self.closed = True


def tool(name, args):
    return {
        "tool_calls": [
            {
                "id": name,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps({"request": json.dumps(args)})},
            }
        ]
    }


def finding(rule, ids):
    return {
        "id": "agent-investigation",
        "criterion_id": rule,
        "title": "Observed support",
        "claim": "Collection assurance still requires independent verification.",
        "outcome": "insufficient_evidence",
        "evidence_ids": ids,
        "counterevidence_ids": [],
        "limitations": ["Synthetic observations only."],
        "method": "agent",
        "details": {},
    }


def agent_plan(dataset, ids):
    plan = datasets.plan_for(SPECS[0].id, dataset=dataset)
    plan["scope"]["required_sources"] = sorted({e["source"] for e in dataset["events"]})
    plan["steps"] = [
        {"id": "select", "title": "Select", "kind": "select", "inputs": [], "config": {"ids": ids}},
        {**plan["steps"][1], "inputs": ["select"]},
    ]
    return plan


async def test_real_pi_scoped_multiturn_and_projection(northstar):
    dataset, _ = northstar
    ids = [e["id"] for e in dataset["events"][:2]]
    plan = agent_plan(dataset, ids)
    model = InferenceDouble(
        [
            tool("search", {"query": "", "filters": {"limit": 1}}),
            tool("fetch", {"ids": ids}),
            tool("join", {"left_ids": ids[:1], "right_ids": ids[1:], "on": "campaign_id"}),
            tool("counterevidence", {"query": "amendment"}),
            {"content": json.dumps({"findings": [finding(plan["id"], ids)]})},
        ]
    )
    result = await execute_plan(plan, dataset, actor=admission(dataset), model=model)
    assert result["usage"]["turns"] == 5 and result["usage"]["tool_calls"] == 4
    assert result["coverage"]["examined_ids"] == sorted(ids)
    assert result["coverage"]["searched_ids"] == sorted(ids)
    assert model.closed
    wire = json.dumps(model.requests)
    assert "native_bytes" not in wire and "bytes_base64" not in wire
    assert dataset["events"][2]["id"] not in wire
    assert result["validation"]["semantic_support"] == "not_independently_verified"


async def test_agent_cannot_fetch_outside_graph_or_subject_scope(northstar):
    dataset, _ = northstar
    first, other = dataset["events"][:2]
    model = InferenceDouble([tool("fetch", {"ids": [other["id"]]})])
    with pytest.raises(RuntimeFailure, match="unauthorized_evidence_reference"):
        await execute_plan(
            agent_plan(dataset, [first["id"]]), dataset, actor=admission(dataset), model=model
        )
    plan = agent_plan(dataset, [first["id"]])
    plan["scope"]["subjects"] = [first["actor"]]
    denied = {**admission(dataset), "authorized_subjects": ["unknown"]}
    with pytest.raises(RuntimeFailure, match="subject_outside_capability"):
        await execute_plan(plan, dataset, actor=denied, model=InferenceDouble([]))


async def test_finding_to_agent_is_context_with_cited_evidence(northstar):
    dataset, _ = northstar
    plan = deterministic_plan(SPECS[0].id, dataset)
    plan["steps"].append({**datasets.plan_for(plan["id"])["steps"][1], "inputs": ["measure"]})
    model = InferenceDouble([{"content": json.dumps({"findings": [finding(plan["id"], [])]})}])
    result = await execute_plan(plan, dataset, actor=admission(dataset), model=model)
    assert len(result["findings"]) == 17
    assert "prior_findings" in json.dumps(model.requests)
    assert "deterministic/domain_audit/v1" in json.dumps(model.requests)
    assert "native_bytes" not in json.dumps(model.requests)


async def test_reauthorization_independent_of_private_events(northstar):
    dataset, _ = northstar
    ids = [dataset["events"][0]["id"]]
    allowed, checks = True, []

    async def authorize():
        checks.append(allowed)
        return allowed

    async def private_event(event):
        nonlocal allowed
        if event["kind"] == "tool_result":
            allowed = False

    model = InferenceDouble([tool("fetch", {"ids": ids}), tool("coverage", {})])
    with pytest.raises(RuntimeFailure, match="authorization_revoked"):
        await execute_plan(
            agent_plan(dataset, ids),
            dataset,
            actor=admission(dataset),
            model=model,
            authorize=authorize,
            on_event=private_event,
        )
    assert len(model.requests) == 1 and model.closed and checks[-1] is False


@pytest.mark.parametrize(
    "config",
    [
        {"rule": "unknown"},
        {"rule": SPECS[0].id, "group_by": "actor"},
        {"rule": SPECS[0].id, "require_complete": False},
    ],
)
async def test_semantic_operator_denial_before_inference(northstar, config):
    dataset, _ = northstar
    plan = datasets.plan_for(SPECS[0].id, dataset=dataset)
    plan["steps"] = list(reversed(plan["steps"]))
    plan["steps"][1]["config"] = config
    model = InferenceDouble([])
    with pytest.raises(RuntimeFailure, match="invalid_rules_config"):
        await execute_plan(plan, dataset, actor=admission(dataset), model=model)
    assert not model.requests


async def test_full_corpus_stream_sql_and_resource_budget(northstar):
    dataset, _ = northstar
    assert len(dataset["events"]) > 1000
    assert len(json.dumps(dataset["events"]).encode()) > 2097152
    computed = await isolated_operation("sql", dataset["events"], sql="SELECT count(*) FROM events")
    assert computed["rows"] == [[len(dataset["events"])]]
    with pytest.raises(RuntimeFailure, match="analysis_input_limit"):
        await isolated_operation(
            "sql", [{"content": "x" * 2097152}], sql="SELECT count(*) FROM events"
        )


async def test_inventory_and_search_do_not_claim_examination(northstar):
    dataset, _ = northstar
    scope = deterministic_plan(SPECS[0].id, dataset)["scope"]
    store = EvidenceStore(dataset, scope=scope, max_results=1)
    try:
        broker = ToolBroker(store)
        assert len(broker.events) > 1
        assert store.coverage()["examined_events"] == 0
        with pytest.raises(EvidenceError, match="result_limit_exceeded"):
            await broker.call("search", {"query": ""})
        assert broker.searched == set() and broker.examined == set()
        await broker.call("search", {"query": "", "filters": {"limit": 1}})
        assert broker.searched == set(broker.by_id)
        assert store.coverage()["examined_events"] == 0
        broker.fetch([broker.events[0]["id"]])
        assert store.coverage()["examined_events"] == 1
    finally:
        store.close()


async def test_deadline_and_cancel_reap_real_pi(northstar, monkeypatch):
    import aiab.workbench.runner as runtime

    dataset, _ = northstar
    ids = [dataset["events"][0]["id"]]
    plan = agent_plan(dataset, ids)
    killed = []
    original = runtime.kill_process

    async def observe(process):
        await original(process)
        killed.append((process.pid, process.returncode))

    monkeypatch.setattr(runtime, "kill_process", observe)
    model = InferenceDouble([], delay=30)
    task = asyncio.create_task(execute_plan(plan, dataset, actor=admission(dataset), model=model))
    try:
        await asyncio.wait_for(model.entered.wait(), 15)
    except BaseException:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert killed and killed[-1][1] is not None and model.closed
    # Allow full-snapshot validation/indexing before exercising a live child
    # deadline (a 1s deadline can correctly expire before a child is launched).
    plan["agent"]["max_seconds"] = 5
    slow = InferenceDouble([], delay=30)
    with pytest.raises(RuntimeFailure, match="runtime_deadline_exceeded"):
        await execute_plan(plan, dataset, actor=admission(dataset), model=slow)
    assert slow.entered.is_set() and slow.closed and len(killed) == 2


async def test_kill_process_reaps_descendant_after_leader_exit(tmp_path):
    # Deliberately create a process tree outside the sandbox to test supervisor
    # cleanup, including a group leader that exits before its descendant.
    pidfile = tmp_path / "descendant"
    program = (
        "import os,time; p=os.fork(); "
        f"(time.sleep(30) if p == 0 else open({str(pidfile)!r},'w').write(str(p)))"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", program, start_new_session=True
    )
    try:
        await asyncio.wait_for(process.wait(), 5)
        descendant = int(pidfile.read_text())
        await kill_process(process)
        # A killed orphan may briefly be a zombie until init reaps it.
        for _ in range(50):
            try:
                state = open(f"/proc/{descendant}/stat").read().split()[2]
            except FileNotFoundError:
                break
            if state == "Z":
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("descendant survived group termination")
    finally:
        await kill_process(process)


async def test_native_snapshot_subject_projection_and_revisions():
    from aiab.workbench.contracts import EVENT_FIELDS, validate_dataset
    from aiab.workbench.ingest import ingest_agent

    dataset = ingest_agent(
        [
            {"id": "revision-1", "actor": "alice", "content": "first", "secret": "native-only"},
            {"id": "revision-2", "actor": "alice", "content": "second", "secret": "native-only"},
            {"id": "outside", "actor": "bob", "content": "other subject"},
        ],
        dataset_id="native-snapshot",
        universe={"accounts": ["alice", "bob"]},
    )
    for i, event in enumerate(dataset["events"][:2]):
        event["native_id"] = "shared-document"
        event["provenance"]["native_revision"] = str(i + 1)
    validate_dataset(dataset)
    plan = agent_plan(dataset, [e["id"] for e in dataset["events"][:2]])
    plan["mode"] = "personal"
    plan["scope"].update(subjects=["alice"], required_sources=["agent"], fields=list(EVENT_FIELDS))
    ids = [e["id"] for e in dataset["events"][:2]]
    model = InferenceDouble(
        [
            tool("fetch", {"ids": ids}),
            {"content": json.dumps({"findings": [finding(plan["id"], ids)]})},
        ]
    )
    result = await execute_plan(plan, dataset, actor="alice", model=model)
    assert result["coverage"]["authorized_events"] == 2
    wire = json.dumps(model.requests)
    assert "native-only" not in wire and "other subject" not in wire
    assert "native_revision" in wire and "shared-document" in wire
    assert dataset["events"][2]["id"] not in wire
    assert len(dataset["native_records"]) == 3


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("max_turns", 1, "turn_budget_exhausted"),
        ("max_tool_calls", 1, "tool_budget_exhausted"),
        ("max_tokens", 100, "token_budget_exhausted"),
    ],
)
async def test_real_contract_resource_budgets(northstar, field, value, error):
    dataset, _ = northstar
    ids = [dataset["events"][0]["id"]]
    plan = agent_plan(dataset, ids)
    plan["agent"][field] = value
    model = InferenceDouble([tool("fetch", {"ids": ids}), tool("coverage", {})])
    with pytest.raises(RuntimeFailure, match=error):
        await execute_plan(plan, dataset, actor=admission(dataset), model=model)
    assert model.closed


async def test_wall_budget_enforced_after_synchronous_checkpoint():
    import time

    from aiab.workbench.ingest import ingest_agent

    dataset = ingest_agent(
        [{"id": "one", "actor": "alice", "content": "observed"}],
        dataset_id="small",
        universe={"accounts": ["alice"]},
    )
    plan = agent_plan(dataset, [dataset["events"][0]["id"]])
    plan["steps"] = plan["steps"][:1]
    plan["agent"]["max_seconds"] = 1

    def checkpoint(event):
        if event["kind"] == "run_started":
            time.sleep(1.05)

    with pytest.raises(RuntimeFailure, match="runtime_deadline_exceeded"):
        await execute_plan(plan, dataset, actor=admission(dataset), on_event=checkpoint)
