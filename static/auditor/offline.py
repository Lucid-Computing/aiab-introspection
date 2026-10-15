"""Offline report verification with an independently pinned, live-verified identity.

The pin is an external trust input. This does not pretend that a TLS observation
is a transferable hardware attestation for a previously unknown report key.
"""

from aiab.audit.plan import Trust, accept_plan
from aiab.audit.protocol import AuditError, digest
from aiab.audit.report import verify_report


def verify_bundle(bundle, anchor, expected_run, expected_input, expected_plan):
    if set(bundle) != {"schema", "signed_plan", "manifest", "identity", "envelope"}:
        raise AuditError("invalid_bundle")
    if bundle["schema"] != "aiab-offline-bundle/v1":
        raise AuditError("invalid_bundle")
    if anchor.get("schema") != "aiab-identity-anchor/v1":
        raise AuditError("invalid_anchor")
    if bundle["identity"] != anchor["identity"]:
        raise AuditError("identity_mismatch")
    if digest(bundle["manifest"]) != anchor["policy_digest"]:
        raise AuditError("policy_mismatch")
    manifest = bundle["manifest"]
    trust = Trust(
        **{
            k: manifest[k]
            for k in (
                "runtime_digest",
                "lab_key",
                "auditor_key",
                "logger_key",
                "model",
                "max_plans",
            )
        }
    )
    if trust.manifest() != manifest:
        raise AuditError("unsupported_manifest")
    accepted = accept_plan(bundle["signed_plan"], trust)
    if accepted.digest != expected_plan:
        raise AuditError("plan_binding_mismatch")
    report = verify_report(
        bundle["envelope"],
        anchor["identity"],
        accepted.value,
        run_id=expected_run,
        input_commitment=expected_input,
        policy_digest=anchor["policy_digest"],
    )
    from auditor.schema_info import capacity

    return {
        **capacity(accepted.value["output_schema"], accepted.value["limits"]["max_result_bytes"]),
        "verified": True,
        "trust_basis": "independently-pinned-live-verified-identity",
        "report": report,
    }
