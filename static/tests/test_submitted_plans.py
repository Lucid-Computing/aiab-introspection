"""The signed plan travels with the submission: verified for that run, never stored."""

import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aiab.audit.api import create_app
from aiab.audit.protocol import AuditError, clone, decode, sign_bytes
from aiab.baked.bundle import check_bundle, file_digest, submitted_bundle
from aiab.baked.records import export_statement, verify
from aiab.baked.runtime import Runtime, verify_report
from aiab.dryrun import MockModel
from tests import test_baked_plan

demo_plan = test_baked_plan.setup  # the frozen demo plan fixture


@pytest.fixture
def plan(demo_plan):
    return demo_plan


TOKENS = {role: role + "-" + "x" * 40 for role in ("lab", "auditor", "logger")}


def signed_document(plan, keys=None, plan_id=None, **edits):
    """The fixture bundle as it is sent with a run, re-signed by the given keys."""
    _, trust, fixture_keys, bundle, _ = plan
    keys = keys or fixture_keys
    files = {name: raw.decode() for name, raw in bundle.files.items()}
    if plan_id:
        cfg = json.loads(files["plan.json"])
        cfg["plan_id"] = plan_id
        files["plan.json"] = json.dumps(cfg, sort_keys=True, separators=(",", ":"))
    files.update(edits)
    digest = file_digest({k: v.encode() for k, v in files.items()})
    signatures = {
        role: base64.b64encode(
            keys[role].sign(sign_bytes("plan-bundle", {"plan_digest": digest}))
        ).decode()
        for role in ("lab", "auditor")
    }
    return {"files": files, "signatures": signatures}, digest


def submission_for(plan, plan_digest, document=None, submission_id="one-shot-1"):
    _, _, keys, bundle, records = plan
    scope = bundle.config["input_scope"]
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
    _, trust, _, baked, _ = plan
    document, digest = signed_document(plan)
    assert submitted_bundle(document, trust).digest == digest == baked.digest


@pytest.mark.parametrize(
    "case",
    [
        "wrong_lab_key",
        "auditor_missing",
        "stale_after_edit",
        "bad_path",
        "extra_field",
        "missing_fixed_file",
    ],
)
def test_submitted_bundle_refused(plan, case):
    _, trust, keys, _, _ = plan
    if case == "wrong_lab_key":
        stranger = {**keys, "lab": Ed25519PrivateKey.generate()}
        document, _ = signed_document(plan, keys=stranger)
        code = "invalid_signature"
    elif case == "auditor_missing":
        document, _ = signed_document(plan)
        del document["signatures"]["auditor"]
        code = "invalid_bundle"
    elif case == "stale_after_edit":
        document, _ = signed_document(plan)
        document["files"]["plan.py"] += "\n"
        code = "invalid_signature"
    elif case == "bad_path":
        document, _ = signed_document(plan)
        document["files"]["prompts/../plan.txt"] = "x"
        code = "invalid_bundle"
    elif case == "extra_field":
        document, _ = signed_document(plan)
        document["note"] = "ignored?"
        code = "invalid_bundle"
    else:
        document, _ = signed_document(plan)
        del document["files"]["plan.py"]
        code = "invalid_bundle"
    with pytest.raises(AuditError, match=code):
        submitted_bundle(document, trust)


def test_submission_names_the_plan_it_carries(plan):
    _, trust, _, baked, _ = plan
    runtime = Runtime(None, trust, MockModel())
    assert "plan_digest" not in runtime.manifest
    # No plan with the records and none baked: nothing to run.
    with pytest.raises(AuditError, match="invalid_plan"):
        runtime.submit(submission_for(plan, baked.digest), "lab")
    # A carried plan whose digest differs from the one the submission names.
    other, _ = signed_document(plan, plan_id="other")
    with pytest.raises(AuditError, match="invalid_plan"):
        runtime.submit(submission_for(plan, baked.digest, other), "lab")
    # A tampered plan never reaches the worker; it fails in admission, unsigned.
    document, digest = signed_document(plan)
    document["files"]["plan.py"] += "\n"
    with pytest.raises(AuditError, match="invalid_signature"):
        runtime.submit(submission_for(plan, digest, document), "lab")
    assert runtime.runs == {}


async def test_empty_box_runs_the_submitted_plan_and_binds_the_report(plan):
    _, trust, _, baked, _ = plan
    runtime = Runtime(None, trust, MockModel())
    app = create_app(runtime, TOKENS)
    document, digest = signed_document(plan, plan_id="sent-with-the-run")
    bundle = check_bundle({k: v.encode() for k, v in document["files"].items()}, trust)
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
        assert report["result"] == {"flagged": False}
        # A verifier holding a different signed plan does not accept this report.
        with pytest.raises(AuditError, match="report_binding_mismatch"):
            verify_report(
                envelope,
                runtime.identity,
                baked,
                runtime.manifest,
                run_id=run_id,
                input_commitment=commitment,
            )
        # Nothing of the plan remains: the same digest without the plan has nothing to run.
        again = submission_for(plan, digest, submission_id="one-shot-2")
        assert (await lab.post("/aiab/runs", json=again)).json() == {"error": "invalid_plan"}
    await runtime.close()


async def test_baked_plan_still_runs_without_a_carried_plan(plan):
    _, trust, _, baked, _ = plan
    runtime = Runtime(baked, trust, MockModel())
    assert runtime.manifest["plan_digest"] == baked.digest
    document, digest = signed_document(plan, plan_id="another-agreement")
    for data in (
        submission_for(plan, baked.digest, submission_id="baked"),
        submission_for(plan, digest, document, submission_id="carried"),
    ):
        run = runtime.submit(clone(data), "lab")
        await run.task
        assert decode(run.envelope)["report"]["plan_digest"] == data["plan_digest"]
    await runtime.close()
