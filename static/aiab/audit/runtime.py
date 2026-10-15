"""Bounded per-boot execution; proof verification precedes model access."""

from __future__ import annotations

import asyncio
import math
import uuid
from dataclasses import dataclass
from typing import Protocol

from aiab.audit.plan import AcceptedPlan, PlanRegistry, Trust
from aiab.audit.protocol import SUBMISSION, AuditError, canonical, clone, digest, validate
from aiab.audit.report import ERRORS, ReportSigner, check_result


class InputVerifier(Protocol):
    def verify(self, text: str, evidence: dict, scope: dict) -> str:
        """Authenticate exact text and complete agreed scope; return a commitment."""
        ...


class Model(Protocol):
    async def classify(self, text: str, policy: dict, model: dict) -> float: ...


@dataclass
class Run:
    run_id: str
    submission_id: str
    fingerprint: str
    plan_digest: str
    input_commitment: str
    envelope: bytes | None = None
    task: asyncio.Task | None = None


class AuditRuntime:
    def __init__(self, trust: Trust, verifier: InputVerifier, model: Model):
        self.registry = PlanRegistry(trust)
        self.verifier, self.model = verifier, model
        self.signer = ReportSigner()
        self.boot_id = uuid.uuid4().hex
        self.manifest = trust.manifest()
        self.policy_digest = digest(self.manifest)
        self.runs: dict[str, Run] = {}
        self.submissions: dict[tuple[str, str], Run] = {}
        self.healthy = True

    @property
    def identity(self) -> dict:
        return {
            "public_key": self.signer.public_key,
            "boot_id": self.boot_id,
            "policy_digest": self.policy_digest,
        }

    def submit(self, submission: dict, principal: str) -> Run:
        validate(submission, SUBMISSION, "invalid_submission")
        plan = self.registry.get(submission["plan_digest"])
        value = plan.value
        text = submission["text"]
        if not text or len(text.encode("utf-8")) > value["limits"]["max_input_utf8_bytes"]:
            raise AuditError("input_too_large", 413)
        commitment = self.verifier.verify(text, submission["logger_evidence"], value["input_scope"])
        fingerprint = digest({"plan_digest": plan.digest, "input_commitment": commitment})
        key = (principal, submission["submission_id"])
        if key in self.submissions:
            prior = self.submissions[key]
            if prior.fingerprint != fingerprint:
                raise AuditError("submission_conflict", 409)
            return prior
        if not self.healthy:
            raise AuditError("model_unavailable", 503)
        active = sum(run.envelope is None for run in self.runs.values())
        if len(self.runs) >= 128 or active >= 4:
            raise AuditError("capacity_exceeded", 429)
        run = Run(uuid.uuid4().hex, key[1], fingerprint, plan.digest, commitment)
        self.submissions[key] = self.runs[run.run_id] = run
        run.task = asyncio.create_task(self._execute(run, plan, text))
        run.task.add_done_callback(lambda _: setattr(run, "task", None))
        return run

    async def _execute(self, run: Run, accepted: AcceptedPlan, text: str) -> None:
        plan = accepted.value
        report = {
            "schema": "aiab-report/v2",
            "run_id": run.run_id,
            "boot_id": self.boot_id,
            "plan_digest": accepted.digest,
            "input_commitment": run.input_commitment,
            "policy_digest": self.policy_digest,
        }

        async def classify(input_text: str, policy: dict) -> float:
            score = await self.model.classify(input_text, policy, clone(plan["model"]))
            if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
                raise AuditError("execution_failed")
            return score

        try:
            async with asyncio.timeout(plan["limits"]["execution_timeout_seconds"]):
                result = await accepted.program.run(text, plan["classification"], classify)
                check_result(result, plan)
            report.update(status="completed", result=result)
        except asyncio.CancelledError:
            report.update(status="failed", error="execution_failed")
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, AuditError) and exc.code in ERRORS
                else "execution_failed"
            )
            if code == "model_unavailable":
                self.healthy = False
            report.update(status="failed", error=code)
        run.envelope = canonical(self.signer.envelope(report))

    async def close(self) -> None:
        self.healthy = False
        tasks = [run.task for run in self.runs.values() if run.task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
