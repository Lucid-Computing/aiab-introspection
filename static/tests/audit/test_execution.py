"""Cross-boundary tests for the signed plan service, using explicit test doubles."""

import asyncio
import base64
from dataclasses import replace

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aiab.audit.api import create_app
from aiab.audit.plan import Trust, accept_plan
from aiab.audit.program import Program
from aiab.audit.protocol import AuditError, clone, decode, digest, sign_bytes, verify_signature
from aiab.audit.report import verify_report
from aiab.audit.runtime import AuditRuntime

PROGRAM = (
    "def run(text, model, policy):\n"
    "    result = model.classify(text, policy)\n"
    '    return {"flagged": result.score >= policy.threshold}\n'
)


@pytest.fixture
def setup():
    keys = {role: Ed25519PrivateKey.generate() for role in ("lab", "auditor", "logger")}
    public = {
        role: base64.b64encode(key.public_key().public_bytes_raw()).decode()
        for role, key in keys.items()
    }
    model = {
        "id": "approved-model",
        "artifact_digest": "sha256:" + "1" * 64,
        "repo": "org/model",
        "release_digest": "sha256:" + "2" * 64,
        "host": "model.tinfoil.dev",
        "temperature": 0,
        "max_output_tokens": 256,
        "context_tokens": 4096,
    }
    trust = Trust("sha256:" + "3" * 64, public["lab"], public["auditor"], public["logger"], model)
    plan = {
        "schema": "aiab-plan/v1",
        "plan_id": "test",
        "runtime_digest": trust.runtime_digest,
        "parties": {"lab_key": public["lab"], "auditor_key": public["auditor"]},
        "input_scope": {
            "logger_key": public["logger"],
            "selection": "all",
            "window_start": "2026-10-15T00:00:00Z",
            "window_end": "2026-10-16T00:00:00Z",
        },
        "model": model,
        "classification": {
            "problem": "RSI",
            "prompt": "Classify evidence.",
            "positive_examples": ["train successor"],
            "negative_examples": ["explain training"],
            "threshold": 0.7,
            "evaluation": {
                "test_set_digest": "sha256:" + "4" * 64,
                "results_digest": "sha256:" + "5" * 64,
            },
        },
        "program": PROGRAM,
        "output_schema": {
            "type": "object",
            "properties": {"flagged": {"type": "boolean"}},
            "required": ["flagged"],
            "additionalProperties": False,
        },
        "limits": {
            "max_input_utf8_bytes": 10000,
            "max_model_calls": 1,
            "execution_timeout_seconds": 1,
            "max_result_bytes": 1024,
        },
    }

    def signed(plan):
        return {
            "plan": clone(plan),
            "signatures": {
                role: base64.b64encode(keys[role].sign(sign_bytes("plan", plan))).decode()
                for role in ("lab", "auditor")
            },
        }

    return trust, plan, signed


class EvidenceDouble:
    def verify(self, text, evidence, scope):
        if evidence != {"test_proof": "accepted"}:
            raise AuditError("invalid_logger_evidence")
        return digest({"text": text, "scope": scope})


class ModelDouble:
    def __init__(self):
        self.calls = 0

    async def classify(self, text, policy, model):
        self.calls += 1
        return 0.9


def test_signatures_and_immutable_plan(setup):
    trust, plan, signed = setup
    document = signed(plan)
    accepted = accept_plan(document, trust)
    document["plan"]["classification"]["threshold"] = 0.99
    assert accepted.value["classification"]["threshold"] == 0.7
    with pytest.raises(AuditError, match="invalid_signature"):
        accept_plan(document, trust)
    with pytest.raises(AuditError, match="unauthorized_signer"):
        accept_plan(signed(plan), replace(trust, auditor_key=trust.lab_key))


@pytest.mark.parametrize(
    "program",
    [
        "import os\n" + PROGRAM,
        "def run(text, model, policy):\n    return model.__class__",
        'def run(text, model, policy):\n    return __import__("os").environ',
        "def run(text, model, policy):\n    while True: pass",
        "def run(text, model, policy):\n    model.classify(text, policy)\n    return {}",
        PROGRAM.replace("result.score", "result.__class__"),
        PROGRAM.replace("return {", "other = model.classify(text, policy)\n    return {"),
    ],
)
def test_program_rejects_capability_escape(program):
    with pytest.raises(AuditError, match="invalid_program"):
        Program(program)


def test_schema_does_not_resolve_external_refs(setup):
    trust, plan, signed = setup
    plan["output_schema"] = {"$ref": "https://evil.example/schema"}
    with pytest.raises(AuditError, match="invalid_output_schema"):
        accept_plan(signed(plan), trust)


def test_duplicate_json_and_nonfinite_rejected():
    for raw in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}'):
        with pytest.raises(AuditError):
            decode(raw)


async def test_api_end_to_end_binding_and_idempotency(setup):
    trust, plan, signed = setup
    model = ModelDouble()
    runtime = AuditRuntime(trust, EvidenceDouble(), model)
    tokens = {k: k * 32 for k in ("lab", "auditor", "logger")}
    app = create_app(runtime, tokens)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as c:
        assert (await c.post("/aiab/plans", json=signed(plan))).status_code == 401
        c.headers["authorization"] = "Bearer " + tokens["lab"]
        response = await c.post("/aiab/plans", json=signed(plan))
        assert response.status_code == 201
        assert (await c.post("/aiab/plans", json=signed(plan))).status_code == 200
        submission = {
            "submission_id": "one",
            "plan_digest": response.json()["plan_digest"],
            "text": "private-sentinel\nmultiple prompts",
            "logger_evidence": {},
        }
        assert (await c.post("/aiab/runs", json=submission)).status_code == 400
        assert model.calls == 0
        submission["logger_evidence"] = {"test_proof": "accepted"}
        admission = (await c.post("/aiab/runs", json=submission)).json()
        duplicate = (await c.post("/aiab/runs", json=submission)).json()
        assert admission["run_id"] == duplicate["run_id"]
        await asyncio.sleep(0)
        report = await c.get(f"/aiab/runs/{admission['run_id']}/report")
        assert report.status_code == 200
        assert "private-sentinel" not in report.text
        assert model.calls == 1
        envelope = report.json()
        commitment = EvidenceDouble().verify(
            submission["text"], submission["logger_evidence"], plan["input_scope"]
        )
        verified = verify_report(
            envelope,
            runtime.identity,
            plan,
            run_id=admission["run_id"],
            input_commitment=commitment,
            policy_digest=runtime.policy_digest,
        )
        assert verified["result"] == {"flagged": True}
        envelope["report"]["result"]["flagged"] = False
        with pytest.raises(AuditError, match="invalid_signature"):
            verify_signature(
                runtime.identity["public_key"], envelope["signature"], "report", envelope["report"]
            )
        submission["text"] = "different"
        assert (await c.post("/aiab/runs", json=submission)).status_code == 409
        c.headers["authorization"] = "Bearer " + tokens["logger"]
        assert (await c.get(f"/aiab/runs/{admission['run_id']}/report")).status_code == 403
    await runtime.close()


async def test_invalid_output_is_signed_failure_without_raw_data(setup):
    trust, plan, signed = setup
    plan["program"] = PROGRAM.replace(
        '{"flagged": result.score >= policy.threshold}', '{"raw": text}'
    )
    runtime = AuditRuntime(trust, EvidenceDouble(), ModelDouble())
    accepted, _ = runtime.registry.register(signed(plan))
    run = runtime.submit(
        {
            "submission_id": "bad",
            "plan_digest": accepted.digest,
            "text": "sentinel-private",
            "logger_evidence": {"test_proof": "accepted"},
        },
        "lab",
    )
    await run.task
    envelope = decode(run.envelope)
    assert envelope["report"]["error"] == "invalid_output"
    assert b"sentinel-private" not in run.envelope
    await runtime.close()


@pytest.mark.parametrize("failure", ["timeout", "unavailable", "malformed"])
async def test_execution_failures_are_explicit_and_signed(setup, failure):
    trust, plan, signed = setup

    class FailingModel:
        async def classify(self, text, policy, model):
            if failure == "timeout":
                await asyncio.sleep(10)
            if failure == "unavailable":
                raise AuditError("model_unavailable")
            return float("nan")

    runtime = AuditRuntime(trust, EvidenceDouble(), FailingModel())
    accepted, _ = runtime.registry.register(signed(plan))
    item = {
        "submission_id": "failure",
        "plan_digest": accepted.digest,
        "text": "sentinel-private",
        "logger_evidence": {"test_proof": "accepted"},
    }
    run = runtime.submit(item, "lab")
    await run.task
    report = decode(run.envelope)
    assert report["report"]["status"] == "failed"
    assert "result" not in report["report"]
    verify_signature(
        runtime.identity["public_key"], report["signature"], "report", report["report"]
    )
    assert b"sentinel-private" not in run.envelope
    if failure == "unavailable":
        assert not runtime.healthy
        item["submission_id"] = "new"
        with pytest.raises(AuditError, match="model_unavailable"):
            runtime.submit(item, "lab")
    await runtime.close()


async def test_capacity_rejects_before_work_and_restart_changes_identity(setup):
    trust, plan, signed = setup
    gate = asyncio.Event()

    class WaitingModel:
        async def classify(self, text, policy, model):
            await gate.wait()
            return 0.9

    runtime = AuditRuntime(trust, EvidenceDouble(), WaitingModel())
    accepted, _ = runtime.registry.register(signed(plan))
    item = {
        "submission_id": "one",
        "plan_digest": accepted.digest,
        "text": "private",
        "logger_evidence": {"test_proof": "accepted"},
    }
    for index in range(4):
        item["submission_id"] = str(index)
        runtime.submit(item, "lab")
    item["submission_id"] = "excess"
    with pytest.raises(AuditError, match="capacity_exceeded"):
        runtime.submit(item, "lab")
    assert len(runtime.runs) == 4
    gate.set()
    await asyncio.gather(*(run.task for run in runtime.runs.values()))
    restarted = AuditRuntime(trust, EvidenceDouble(), ModelDouble())
    assert restarted.identity != runtime.identity
    assert not restarted.runs
    await runtime.close()
    await restarted.close()
