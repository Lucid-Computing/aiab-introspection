"""Test doubles are explicitly labeled; deterministic integration uses the real sandbox."""

import asyncio
import copy
import json

import pytest
from aiab.workbench import datasets, runner
from aiab.workbench.fixtures.specs import SPECS
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import workbench_assist as assist
from server.workbench_api import WorkbenchAPI


@pytest.fixture
def api(tmp_path):
    workbench = WorkbenchAPI(
        tmp_path,
        lambda r: {"username": r.headers["test-user"]} if r.headers.get("test-user") else None,
    )
    router = assist.create_router(workbench)
    app = FastAPI()
    app.include_router(workbench.router)
    app.include_router(router)
    with TestClient(app) as client:
        client.headers.update({"test-user": "alice", "origin": "http://testserver"})
        yield workbench, router.evaluation_jobs, client
        client.portal.call(drain, router.evaluation_jobs)


async def drain(jobs):
    await asyncio.gather(*list(jobs.tasks.values()), return_exceptions=True)


@pytest.fixture(scope="module")
def observed():
    return datasets.generate(0)


def plan(observed, rule="information-exposure"):
    return datasets.plan_for(rule, dataset=observed)


def save(api, value):
    response = api[2].post("/api/workbench/plans", json={"plan": value})
    assert response.status_code == 200, response.text


class ExplicitTestModel:
    is_test_model = True

    def __init__(self, candidate):
        self.message = {"role": "assistant", "content": json.dumps({"plan": candidate})}
        self.started = self.closed = False
        self.messages = None

    async def start(self):
        self.started = True

    async def count_tokens(self, messages, tools):
        self.messages = copy.deepcopy(messages)
        assert tools == []
        return 100

    async def complete(self, messages, tools, max_tokens):
        assert self.started and max_tokens == assist.MAX_OUTPUT_TOKENS and tools == []
        return {
            "message": self.message,
            "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
        }

    async def close(self):
        self.closed = True


def test_counterproposal_visible_scope_identity_and_no_write(observed):
    original = plan(observed)
    original["scope"]["identities"] = [{"subject": "person", "aliases": ["handle"]}]
    candidate = copy.deepcopy(original)
    candidate["scope"]["subjects"].append("additional-person")
    model = ExplicitTestModel(candidate)
    result = asyncio.run(assist.suggest(original, "Add additional-person", test_model=model))
    assert model.closed
    assert result["verification"] == {"test_model": True}
    assert result["usage"]["test_model"] is True
    assert result["changes"] == [
        {
            "path": "/scope/subjects",
            "before": original["scope"]["subjects"],
            "after": candidate["scope"]["subjects"],
        }
    ]
    assert json.loads(model.messages[1]["content"]) == {
        "plan": original,
        "instruction": "Add additional-person",
    }
    assert "world_truth" not in model.messages[1]["content"]
    candidate["scope"]["identities"] = []
    with pytest.raises(ValueError, match="identity mapping"):
        asyncio.run(assist.suggest(original, "Change map", test_model=ExplicitTestModel(candidate)))


@pytest.mark.parametrize(
    "failure", ["tools", "duplicate", "markdown", "identity", "operator", "envelope"]
)
def test_malformed_model_proposals_close_and_fail(observed, failure):
    candidate = plan(observed)
    model = ExplicitTestModel(candidate)
    if failure == "tools":
        model.message["tool_calls"] = [{"id": "forbidden"}]
    elif failure == "duplicate":
        model.message["content"] = '{"plan":{},"plan":{}}'
    elif failure == "markdown":
        model.message["content"] = "```json\n{}\n```"
    elif failure == "identity":
        candidate["id"] = "changed"
        model.message["content"] = json.dumps({"plan": candidate})
    elif failure == "operator":
        candidate["steps"][0]["config"]["operator"] = "unknown"
        model.message["content"] = json.dumps({"plan": candidate})
    else:
        model.message["content"] = json.dumps({"plan": candidate, "execute": True})
    with pytest.raises(ValueError):
        asyncio.run(assist.suggest(plan(observed), "Improve", test_model=model))
    assert model.closed


def test_suggestion_deadline_closes_model(observed, monkeypatch):
    model = ExplicitTestModel(plan(observed))

    async def blocked(*args):
        await asyncio.Future()

    model.complete = blocked
    monkeypatch.setattr(assist, "SUGGESTION_SECONDS", 0.02)
    with pytest.raises(TimeoutError):
        asyncio.run(assist.suggest(plan(observed), "Improve", test_model=model))
    assert model.closed
    model.is_test_model = False
    with pytest.raises(ValueError, match="labeled test"):
        asyncio.run(assist.suggest(plan(observed), "Improve", test_model=model))


def test_auth_origin_owner_input_limits_and_no_http_injection(api, observed):
    workbench, jobs, client = api
    value = plan(observed)
    save(api, value)
    url = "/api/workbench/evaluations"
    assert (
        client.post(
            url, json={"plan_id": value["id"]}, headers={"origin": "https://evil"}
        ).status_code
        == 403
    )
    assert (
        client.post(url, json={"plan_id": value["id"]}, headers={"test-user": "bob"}).status_code
        == 404
    )
    for body in [
        {"plan_id": value["id"], "include_agent": "false"},
        {"plan_id": value["id"], "case_limit": True},
        {"plan_id": value["id"], "include_agent": True, "case_limit": 5},
        {"plan_id": value["id"], "seed": -1},
        {
            "plan_id": value["id"],
            "evidence_mode": "synthetic_full_trace",
            "authorization": {"all": True},
        },
    ]:
        assert client.post(url, json=body).status_code == 400
    assert not jobs.tasks
    assert (
        client.post(
            "/api/workbench/suggestions",
            json={"plan": value, "instruction": "x", "test_model": True},
        ).status_code
        == 400
    )
    del client.headers["test-user"]
    assert client.post(url, json={"plan_id": value["id"]}).status_code == 401
    assert client.get(url + "/unknown").status_code == 401


def test_real_deterministic_api_durable_derived_scope_and_metrics(api, observed):
    workbench, jobs, client = api
    value = plan(observed)
    save(api, value)
    response = client.post("/api/workbench/evaluations", json={"plan_id": value["id"]})
    assert response.status_code == 200, response.text
    ident = response.json()["evaluation_id"]
    client.portal.call(drain, jobs)
    result = client.get("/api/workbench/evaluations/" + ident).json()
    assert result["status"] == "completed", result
    assert result["metrics"]["cases"] == result["metrics"]["exact_matches"] == 16, result
    assert result["metrics"]["accuracy_denominator"] == 16
    assert result["metrics"]["unknown"] == result["metrics"]["citation_errors"] == 0
    assert result["evaluated_plan"]["steps"] == value["steps"][:1]
    assert result["evaluated_plan"]["scope"] == value["scope"]
    assert result["changes"]
    assert result["report"]["model_accuracy_claim"] is False
    assert all(r["usage"]["turns"] == 0 for r in result["report"]["cases"])
    for forbidden in [
        "world_truth",
        "expected_supportable",
        "scripted_intervention",
        '"variant"',
        '"family"',
    ]:
        assert forbidden not in json.dumps(result)
    assert assist.EvaluationJobs(workbench).get("alice", ident)["metrics"] == result["metrics"]
    for suffix in ["", "/cancel"]:
        endpoint = "/api/workbench/evaluations/" + ident + suffix
        denied = (
            client.post(endpoint, headers={"test-user": "bob"})
            if suffix
            else client.get(endpoint, headers={"test-user": "bob"})
        )
        assert denied.status_code == 404
    assert workbench.store.plan("alice", value["id"])["plan"] == value


@pytest.mark.parametrize("spec", SPECS, ids=lambda spec: spec.id)
def test_all_27_selected_methods_real_sandbox(observed, spec):
    value = plan(observed, spec.id)
    value["steps"] = value["steps"][:1]
    report, metrics = asyncio.run(assist.evaluate(value, "alice", 16, 0, False))
    assert metrics["exact_matches"] == metrics["cases"] == 16, (spec.id, metrics, report)
    assert metrics["unknown"] == metrics["citation_errors"] == 0


def test_failure_missing_predictions_and_abstention_not_clean_negatives(observed, monkeypatch):
    value = plan(observed)
    value["steps"] = value["steps"][:1]

    async def broken(*args, **kwargs):
        raise ValueError("PRIVATE_SECRET")

    monkeypatch.setattr(runner, "execute_plan", broken)
    report, metrics = asyncio.run(assist.evaluate(value, "alice", 16, 0, False))
    assert metrics["unknown"] == metrics["accuracy_denominator"] == 16
    assert metrics["accuracy"] == 0 and metrics["tn"] == 0
    assert metrics["precision"] is None
    assert metrics["recall"] == 0
    assert "PRIVATE_SECRET" not in json.dumps(report)


def test_scope_not_widened_and_truth_never_passed_to_runtime(observed, monkeypatch):
    value = plan(observed)
    value["steps"] = value["steps"][:1]
    value["scope"]["subjects"] = ["nobody"]
    original = runner.execute_plan

    async def inspect(plan_arg, dataset_arg, **kwargs):
        assert plan_arg["scope"] == value["scope"]
        text = json.dumps(dataset_arg)
        assert "world_truth" not in text and "expected_supportable" not in text
        investigations = {e["attributes"]["investigation_id"] for e in dataset_arg["events"]}
        assert len(investigations) == 1
        return await original(plan_arg, dataset_arg, **kwargs)

    monkeypatch.setattr(runner, "execute_plan", inspect)
    report, metrics = asyncio.run(assist.evaluate(value, "alice", 4, 0, False))
    assert metrics["exact_matches"] == 0
    assert metrics["unknown"] == 4
    assert report["excluded_by_limit"] == 12


def test_cancel_restart_and_late_completion_cannot_overwrite(api, observed, monkeypatch):
    workbench, jobs, client = api
    value = plan(observed)
    save(api, value)

    async def blocked(*args, **kwargs):
        await asyncio.Future()

    monkeypatch.setattr(assist, "evaluate", blocked)
    response = client.post("/api/workbench/evaluations", json={"plan_id": value["id"]})
    ident = response.json()["evaluation_id"]
    response = client.post(f"/api/workbench/evaluations/{ident}/cancel")
    assert response.json()["status"] == "cancelled"
    client.portal.call(drain, jobs)
    assert not jobs.update("alice", ident, "completed", {"private": True})
    assert jobs.get("alice", ident)["status"] == "cancelled"
    ident = client.post("/api/workbench/evaluations", json={"plan_id": value["id"]}).json()[
        "evaluation_id"
    ]
    restarted = assist.EvaluationJobs(workbench)
    assert restarted.get("alice", ident)["status"] == "interrupted"
    assert not restarted.tasks
    client.portal.call(jobs.tasks[ident].cancel)
    client.portal.call(drain, jobs)
    assert restarted.get("alice", ident)["status"] == "interrupted"


def test_agent_admission_uses_real_runtime_path_without_model_injection(api, observed, monkeypatch):
    workbench, jobs, client = api
    value = plan(observed)
    save(api, value)
    calls = []

    async def runtime_failure(actual_plan, actual_data, **kwargs):
        calls.append((actual_plan, actual_data, kwargs))
        raise runner.RuntimeFailure("verified_transport_failed")

    monkeypatch.setattr(runner, "execute_plan", runtime_failure)
    response = client.post(
        "/api/workbench/evaluations",
        json={"plan_id": value["id"], "include_agent": True, "case_limit": 1},
    )
    assert response.status_code == 200, response.text
    ident = response.json()["evaluation_id"]
    client.portal.call(drain, jobs)
    job = jobs.get("alice", ident)
    assert len(calls) == 1
    assert calls[0][0] == value
    assert "model" not in calls[0][2]
    assert calls[0][1]["origin"] == "synthetic"
    assert job["metrics"]["unknown"] == job["metrics"]["cases"] == 1
    assert job["metrics"]["accuracy"] == 0
    assert job["changes"] == []
    assert job["report"]["execution"] == "reviewed_deepseek_and_plan_tools"
    assert job["report"]["cases"][0]["failure"] == "execution_failed"
    # The case report was durably checkpointed, then completed without substituting
    # deterministic predictions for failed model execution.
    assert assist.EvaluationJobs(workbench).get("alice", ident)["report"] == job["report"]


def test_cancel_preserves_partial_checkpoint_and_capacity(api, observed, monkeypatch):
    _, jobs, client = api
    value = plan(observed)
    save(api, value)

    async def partial(*args, on_case, **kwargs):
        await on_case([{"status": "unknown", "failure": "execution_failed"}])
        await asyncio.Future()

    monkeypatch.setattr(assist, "evaluate", partial)
    identifiers = []
    for _ in range(2):
        response = client.post("/api/workbench/evaluations", json={"plan_id": value["id"]})
        assert response.status_code == 200
        identifiers.append(response.json()["evaluation_id"])
    assert (
        client.post("/api/workbench/evaluations", json={"plan_id": value["id"]}).status_code == 429
    )
    for ident in identifiers:
        assert jobs.get("alice", ident)["report"]["partial"]
        assert client.post(f"/api/workbench/evaluations/{ident}/cancel").status_code == 200
        job = jobs.get("alice", ident)
        assert job["status"] == "cancelled"
        assert len(job["report"]["cases"]) == 1
    client.portal.call(drain, jobs)


@pytest.mark.parametrize("key", ["private_key", "events", "world_truth", "credentials"])
def test_authoring_rejects_embedded_evidence_and_secrets_before_model(observed, key):
    value = plan(observed)
    value[key] = "PRIVATE"
    model = ExplicitTestModel(value)
    with pytest.raises(ValueError, match="forbidden"):
        asyncio.run(assist.suggest(value, "Improve", test_model=model))
    assert not model.started
