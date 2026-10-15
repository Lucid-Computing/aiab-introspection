"""One baked agreement per boot, bounded run state, independent report signing."""

import asyncio
import base64
import uuid
from dataclasses import dataclass

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
from aiab.audit.report import ERRORS, ReportSigner
from aiab.baked import records
from aiab.baked.bundle import restrictions
from aiab.baked.executor import execute
from aiab.baked.output import capacity, check_result

BASE = {
    "schema": {"const": "aiab-report/v3"},
    "aiab_id": ID,
    "boot_id": ID,
    "run_id": ID,
    "image_digest": DIGEST,
    "plan_digest": DIGEST,
    "policy_digest": DIGEST,
    "input_commitment": DIGEST,
    "egress_max_bits": {"type": "integer", "minimum": 0, "maximum": 32768},
}
REPORT = {
    "oneOf": [
        closed({**BASE, "status": {"const": "completed"}, "result": {}}),
        closed({**BASE, "status": {"const": "failed"}, "error": {"enum": list(ERRORS)}}),
    ]
}


@dataclass
class Run:
    run_id: str
    fingerprint: str
    envelope: bytes | None = None
    task: asyncio.Task | None = None


class Runtime:
    def __init__(self, bundle, trust, model, aiab_id="aiab-baked-audit"):
        self.bundle, self.trust, self.model, self.aiab_id = bundle, trust, model, aiab_id
        self.signer = ReportSigner()
        self.boot_id = uuid.uuid4().hex
        self.manifest = {
            "schema": "aiab-baked-manifest/v1",
            "aiab_id": aiab_id,
            "image_digest": trust["runtime_digest"],
            "plan_digest": bundle.digest,
            "plan_id": bundle.config["plan_id"],
            "restrictions": restrictions(trust),
            "model_binding": trust["model"],
            "parties": {k: trust[k] for k in ("lab_key", "auditor_key", "logger_key")},
            "report_schema": "aiab-report/v3",
            "max_active_runs": 4,
            "max_retained_runs": 128,
            "retention": "in-memory-per-boot",
            "logger_evidence": "aiab-record-export/v1",
            "logger_integration": "synthetic-export-contract-not-native-WORM",
            "egress_max_bits": capacity(bundle.config),
        }
        self.policy_digest = digest(self.manifest)
        self.runs, self.submissions = {}, {}
        self.healthy = True

    @property
    def identity(self):
        return {
            "public_key": self.signer.public_key,
            "boot_id": self.boot_id,
            "policy_digest": self.policy_digest,
        }

    def submit(self, submission, principal):
        validate(submission, records.SUBMISSION, "invalid_submission")
        if submission["plan_digest"] != self.bundle.digest:
            raise AuditError("invalid_plan")
        commitment = records.verify(submission, self.bundle.config, self.trust["logger_key"])
        fingerprint = digest({"plan_digest": self.bundle.digest, "input_commitment": commitment})
        key = (principal, submission["submission_id"])
        if key in self.submissions:
            run = self.submissions[key]
            if run.fingerprint != fingerprint:
                raise AuditError("submission_conflict", 409)
            return run
        if not self.healthy:
            raise AuditError("model_unavailable", 503)
        if len(self.runs) >= 128 or sum(r.envelope is None for r in self.runs.values()) >= 4:
            raise AuditError("capacity_exceeded", 429)
        run = Run(uuid.uuid4().hex, fingerprint)
        self.runs[run.run_id] = self.submissions[key] = run
        run.task = asyncio.create_task(self._execute(run, submission["records"], commitment))
        run.task.add_done_callback(lambda _: setattr(run, "task", None))
        return run

    async def _execute(self, run, input_records, commitment):
        report = {
            "schema": "aiab-report/v3",
            "aiab_id": self.aiab_id,
            "boot_id": self.boot_id,
            "run_id": run.run_id,
            "image_digest": self.trust["runtime_digest"],
            "plan_digest": self.bundle.digest,
            "policy_digest": self.policy_digest,
            "input_commitment": commitment,
            "egress_max_bits": capacity(self.bundle.config),
        }
        try:
            result = await execute(self.bundle, input_records, self.model)
            report.update(status="completed", result=result)
        except (Exception, asyncio.CancelledError) as exc:
            error = (
                exc.code
                if isinstance(exc, AuditError) and exc.code in ERRORS
                else "execution_failed"
            )
            if error == "model_unavailable":
                self.healthy = False
            report.update(status="failed", error=error)
        validate(report, REPORT, "invalid_report")
        run.envelope = canonical(
            {
                "report": report,
                "signature": base64.b64encode(
                    self.signer._key.sign(sign_bytes("report", report))
                ).decode(),
            }
        )

    async def close(self):
        self.healthy = False
        tasks = [r.task for r in self.runs.values() if r.task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def verify_report(envelope, identity, bundle, manifest, *, run_id, input_commitment):
    validate(
        envelope, closed({"report": REPORT, "signature": {"type": "string"}}), "invalid_report"
    )
    report = envelope["report"]
    verify_signature(identity["public_key"], envelope["signature"], "report", report)
    expected = {
        "aiab_id": manifest["aiab_id"],
        "boot_id": identity["boot_id"],
        "run_id": run_id,
        "image_digest": manifest["image_digest"],
        "plan_digest": bundle.digest,
        "policy_digest": digest(manifest),
        "input_commitment": input_commitment,
        "egress_max_bits": capacity(bundle.config),
    }
    if (
        any(report[k] != v for k, v in expected.items())
        or identity["policy_digest"] != digest(manifest)
        or manifest["plan_digest"] != bundle.digest
    ):
        raise AuditError("report_binding_mismatch")
    if report["status"] == "completed":
        check_result(report["result"], bundle.config)
    return report
