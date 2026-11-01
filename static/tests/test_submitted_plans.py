"""The signed plan travels with every submission: verified for that run, never stored."""

import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aiab.audit.api import create_app
from aiab.audit.protocol import AuditError, sign_bytes
from aiab.baked.bundle import PLAN_FILE, check_bundle, file_digest, submitted_bundle
from aiab.baked.records import export_statement, scope_of, verify
from aiab.baked.runtime import Runtime, verify_report
from aiab.dryrun import MockModel
from tests import test_baked_plan

demo_plan = test_baked_plan.setup  # the frozen graph-plan fixture


@pytest.fixture
def plan(demo_plan):
    return demo_plan


TOKENS = {role: role + "-" + "x" * 40 for role in ("lab", "auditor", "logger")}


def signed_document(plan, keys=None, plan_id=None, text=None):
    """The fixture plan as it is sent with a run, re-signed by the given keys."""
    _, trust, fixture_keys, bundle, _ = plan
    keys = keys or fixture_keys
    if text is None:
        text = bundle.files[PLAN_FILE].decode()
        if plan_id:
            document = json.loads(text)
            document["plan_id"] = plan_id
            text = json.dumps(document, sort_keys=True, separators=(",", ":"))
    digest = file_digest({PLAN_FILE: text.encode()})
    signatures = {
        role: base64.b64encode(
            keys[role].sign(sign_bytes("plan-bundle", {"plan_digest": digest}))
        ).decode()
        for role in ("lab", "auditor")
    }
    return {"files": {PLAN_FILE: text}, "signatures": signatures}, digest


def submission_for(plan, plan_digest, document=None, submission_id="one-shot-1"):
    _, _, keys, bundle, records = plan
    scope = bundle.config.get("input_scope") or scope_of(records)
    statement = export_statement(records, scope)
    return {
        "submission_id": submission_id,
        "plan_digest": plan_digest,
        "input_scope": scope,
        "records": records,
        "logger_evidence": {
            "statement": statement,
            "signature": base64.b64encode(
                keys["logger"].sign(sign_bytes("record-export", statement))
            ).decode(),
        },
        **({"plan": document} if document is not None else {}),
    }


def test_submitted_bundle_has_the_folder_digest(plan):
    _, trust, _, folder_bundle, _ = plan
    document, digest = signed_document(plan)
    assert submitted_bundle(document, trust).digest == digest == folder_bundle.digest


@pytest.mark.parametrize(
    "case",
    [
        "wrong_lab_key",
        "auditor_missing",
        "stale_after_edit",
        "extra_file",
        "extra_field",
        "no_plan_file",
        "not_a_plan",
    ],
)
def test_submitted_bundle_refused(plan, case):
    _, trust, keys, _, _ = plan
    document, _ = signed_document(plan)
    code = "invalid_bundle"
    if case == "wrong_lab_key":
        stranger = {**keys, "lab": Ed25519PrivateKey.generate()}
        document, _ = signed_document(plan, keys=stranger)
        code = "invalid_signature"
    elif case == "auditor_missing":
        del document["signatures"]["auditor"]
    elif case == "stale_after_edit":
        document["files"][PLAN_FILE] += "\n"
        code = "invalid_signature"
    elif case == "extra_file":
        document["files"]["prompts/../x.txt"] = "x"
    elif case == "extra_field":
        document["note"] = "ignored?"
    elif case == "no_plan_file":
        document["files"] = {}
    else:
        document, _ = signed_document(plan, text='{"not": "a plan"}')
        code = "invalid_plan"
    with pytest.raises(AuditError, match=code):
        submitted_bundle(document, trust)


def test_submission_names_the_plan_it_carries(plan):
    _, trust, _, folder_bundle, _ = plan
    runtime = Runtime(trust, MockModel())
    assert "plan_digest" not in runtime.manifest
    # No plan with the records: not a submission.
    with pytest.raises(AuditError, match="invalid_submission"):
        runtime.submit(submission_for(plan, folder_bundle.digest), "lab")
    # A carried plan whose digest differs from the one the submission names.
    other, _ = signed_document(plan, plan_id="other")
    with pytest.raises(AuditError, match="invalid_plan"):
        runtime.submit(submission_for(plan, folder_bundle.digest, other), "lab")
    # A tampered plan never reaches the worker; it fails in admission, unsigned.
    document, digest = signed_document(plan)
    document["files"][PLAN_FILE] += "\n"
    with pytest.raises(AuditError, match="invalid_signature"):
        runtime.submit(submission_for(plan, digest, document), "lab")
    assert runtime.runs == {}


async def test_empty_box_runs_the_submitted_plan_and_binds_the_report(plan):
    _, trust, _, folder_bundle, _ = plan
    runtime = Runtime(trust, MockModel())
    app = create_app(runtime, TOKENS)
    document, digest = signed_document(plan, plan_id="sent-with-the-run")
    bundle = check_bundle({PLAN_FILE: document["files"][PLAN_FILE].encode()}, trust)
    data = submission_for(plan, digest, document)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"authorization": "Bearer " + TOKENS["logger"]},
    ) as logger:
        assert (await logger.post("/aiab/plans", json=document)).status_code == 403
        response = await logger.post("/aiab/runs", json=data)
        assert response.status_code == 202 and response.json()["plan_digest"] == digest
        run_id = response.json()["run_id"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"authorization": "Bearer " + TOKENS["lab"]},
    ) as lab:
        assert (await lab.post("/aiab/plans", json=document)).status_code == 405
        await runtime.runs[run_id].task
        envelope = (await lab.get(f"/aiab/runs/{run_id}/report")).json()
        assert envelope["report"]["plan_digest"] == digest
        commitment = verify(data, bundle.config, trust["logger_key"])
        report = verify_report(
            envelope,
            runtime.identity,
            bundle,
            runtime.manifest,
            run_id=run_id,
            input_commitment=commitment,
        )
        assert report["result"] == {"flagged": False, "destructive_sessions": 0, "sessions": 1}
        # A verifier holding a different signed plan does not accept this report.
        with pytest.raises(AuditError, match="report_binding_mismatch"):
            verify_report(
                envelope,
                runtime.identity,
                folder_bundle,
                runtime.manifest,
                run_id=run_id,
                input_commitment=commitment,
            )
        # Nothing of the plan remains: a run must bring the plan again.
        again = submission_for(plan, digest, submission_id="one-shot-2")
        assert (await lab.post("/aiab/runs", json=again)).json() == {"error": "invalid_submission"}
    await runtime.close()
