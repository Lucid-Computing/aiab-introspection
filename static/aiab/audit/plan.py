"""Immutable jointly approved plans and independently configured trust roots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from aiab.audit.program import Program
from aiab.audit.protocol import (
    SIGNED_PLAN,
    AuditError,
    canonical,
    check_output_schema,
    clone,
    decode,
    digest,
    validate,
    verify_signature,
)


@dataclass(frozen=True)
class Trust:
    runtime_digest: str
    lab_key: str
    auditor_key: str
    logger_key: str
    model: dict
    max_plans: int = 16

    def manifest(self) -> dict:
        return {
            "schema": "aiab-audit-manifest/v1",
            "runtime_digest": self.runtime_digest,
            "lab_key": self.lab_key,
            "auditor_key": self.auditor_key,
            "logger_key": self.logger_key,
            "model": clone(self.model),
            "program_language": "aiab-python-expressions/v1",
            "canonicalization": "RFC8785",
            "max_plans": self.max_plans,
            "max_retained_runs": 128,
            "max_active_runs": 4,
            "retention": "bounded-per-boot-no-eviction",
            "logger_evidence": "aiab-logger-export/v1",
            "logger_completeness": "trusted-exporter-signed-assertion",
            "output_schema_subset": "closed-objects-booleans-bounded-integers-string-enums/v1",
            "report_schema": "aiab-report/v2",
            "max_http_body_bytes": 2097152,
            "max_admission_requests": 8,
            "max_admission_seconds": 15,
            "classifier_retry_count": 0,
            "model_binding": "reviewed-serving-release-modelwrap/v1",
        }


@dataclass(frozen=True)
class AcceptedPlan:
    encoded: bytes
    digest: str

    @property
    def value(self) -> dict:
        return decode(self.encoded)

    @property
    def program(self) -> Program:
        return Program(self.value["program"])


def accept_plan(document: dict, trust: Trust) -> AcceptedPlan:
    validate(document, SIGNED_PLAN)
    plan = document["plan"]
    if plan["parties"] != {"lab_key": trust.lab_key, "auditor_key": trust.auditor_key}:
        raise AuditError("unauthorized_signer", 403)
    if trust.lab_key == trust.auditor_key:
        raise AuditError("unauthorized_signer", 403)
    for role, key in (("lab", trust.lab_key), ("auditor", trust.auditor_key)):
        verify_signature(key, document["signatures"][role], "plan", plan)
    if plan["runtime_digest"] != trust.runtime_digest or plan["model"] != trust.model:
        raise AuditError("unapproved_runtime_or_model")
    scope = plan["input_scope"]
    if scope["logger_key"] != trust.logger_key:
        raise AuditError("unapproved_logger")
    if datetime.fromisoformat(scope["window_start"]) >= datetime.fromisoformat(scope["window_end"]):
        raise AuditError("invalid_scope")
    check_output_schema(plan["output_schema"])
    Program(plan["program"])
    return AcceptedPlan(canonical(plan), digest(plan))


class PlanRegistry:
    def __init__(self, trust: Trust):
        self.trust = trust
        self._plans: dict[str, AcceptedPlan] = {}

    def register(self, document: dict) -> tuple[AcceptedPlan, bool]:
        plan = accept_plan(document, self.trust)
        if plan.digest in self._plans:
            return self._plans[plan.digest], False
        if len(self._plans) >= self.trust.max_plans:
            raise AuditError("capacity_exceeded", 429)
        self._plans[plan.digest] = plan
        return plan, True

    def get(self, plan_digest: str) -> AcceptedPlan:
        try:
            return self._plans[plan_digest]
        except KeyError:
            raise AuditError("invalid_plan") from None
