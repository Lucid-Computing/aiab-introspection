"""Fixed signed envelopes; the plan alone specifies the result schema."""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aiab.audit.protocol import (
    DIGEST,
    ID,
    AuditError,
    canonical,
    closed,
    digest,
    sign_bytes,
    validate,
    verify_signature,
)

ERRORS = ("execution_failed", "model_unavailable", "invalid_output", "input_too_large")
BASE = {
    "schema": {"const": "aiab-report/v2"},
    "run_id": ID,
    "boot_id": ID,
    "plan_digest": DIGEST,
    "input_commitment": DIGEST,
    "policy_digest": DIGEST,
}
REPORT_SCHEMA = {
    "oneOf": [
        closed({**BASE, "status": {"const": "completed"}, "result": {}}),
        closed({**BASE, "status": {"const": "failed"}, "error": {"enum": list(ERRORS)}}),
    ]
}


class ReportSigner:
    def __init__(self):
        self._key = Ed25519PrivateKey.generate()

    @property
    def public_key(self) -> str:
        return base64.b64encode(self._key.public_key().public_bytes_raw()).decode()

    def envelope(self, report: dict) -> dict:
        validate(report, REPORT_SCHEMA, "invalid_report")
        return {
            "report": report,
            "signature": base64.b64encode(self._key.sign(sign_bytes("report", report))).decode(),
        }


def check_result(result: object, plan: dict) -> None:
    try:
        validate(result, plan["output_schema"], "invalid_output")
        if len(canonical(result)) > plan["limits"]["max_result_bytes"]:
            raise AuditError("invalid_output")
    except (ValueError, TypeError, RecursionError):
        raise AuditError("invalid_output") from None


def verify_report(
    envelope: dict,
    identity: dict,
    plan: dict,
    *,
    run_id: str,
    input_commitment: str,
    policy_digest: str,
) -> dict:
    validate(
        envelope,
        closed({"report": REPORT_SCHEMA, "signature": {"type": "string"}}),
        "invalid_report",
    )
    report = envelope["report"]
    verify_signature(identity["public_key"], envelope["signature"], "report", report)
    for field, expected in {
        "run_id": run_id,
        "boot_id": identity["boot_id"],
        "plan_digest": digest(plan),
        "input_commitment": input_commitment,
        "policy_digest": policy_digest,
    }.items():
        if report[field] != expected:
            raise AuditError("report_binding_mismatch")
    if identity["policy_digest"] != policy_digest:
        raise AuditError("report_binding_mismatch")
    if report["status"] == "completed":
        check_result(report["result"], plan)
    return report
