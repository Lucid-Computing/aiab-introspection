"""Admission-time accounting, frozen policy manifests, and signed finalized periods."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from aiab.classifier import SERIALIZATION_VERSION, Classifier, ClassifyResult, build_system_prompt
from aiab.config import Config, origin
from aiab.keys import SigningKey

COUNTERS = (
    "requests_total",
    "requests_forwarded",
    "requests_selected",
    "requests_classified",
    "requests_flagged",
    "requests_blocked",
    "classifier_errors",
)
REPORT_FIELDS = frozenset(
    (
        "schema",
        "aiab_id",
        "boot_id",
        "sequence",
        "policy_digest",
        "classifier_id",
        "enforcement_mode",
        "period_start",
        "period_end",
        *COUNTERS,
    )
)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def policy_digest(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


def build_manifest(config: Config, classifier: Classifier) -> dict[str, Any]:
    """An explicit allowlist of non-secret effective policy settings."""
    return {
        "schema": "aiab-manifest/v1",
        "classifier_id": classifier.id,
        "classifier_implementation": classifier.implementation,
        "definition_sha256": hashlib.sha256(config.definition_text.encode("utf-8")).hexdigest(),
        "system_prompt_sha256": hashlib.sha256(
            build_system_prompt(config.definition_text).encode("utf-8")
        ).hexdigest(),
        "requested_model": config.classifier_model,
        "classifier_endpoint": "local:mock",
        "classifier_verification_policy": "local-development-unattested",
        "generation_parameters": {},  # The local keyword implementation has no generation.
        "input_serialization_version": SERIALIZATION_VERSION,
        "enforcement_mode": config.enforcement_mode,
        "block_threshold": config.block_threshold,
        "sample_rate": config.sample_rate,
        "sampling_algorithm": "secrets.SystemRandom/Bernoulli",
        "report_period_seconds": config.report_period_seconds,
        "report_history": config.report_history,
        "processing_timeout_seconds": config.processing_timeout_seconds,
        "classifier_attempt_timeout_seconds": config.classifier_attempt_timeout_seconds,
        "classifier_attempts": config.classifier_attempts,
        "upstream_timeout_seconds": config.upstream_timeout_seconds,
        "monitor_queue_limit": config.monitor_queue_limit,
        "monitor_workers": config.monitor_workers,
        "classifier_concurrency": config.classifier_concurrency,
        "max_request_bytes": config.max_request_bytes,
        "inference_origin": origin(config.inference_url),
    }


def validate_counters(report: dict[str, Any]) -> None:
    for key in COUNTERS:
        if type(report[key]) is not int or report[key] < 0:
            raise ValueError("Report counters must be non-negative integers")
    total, forwarded, selected, classified, flagged, blocked, errors = (
        report[key] for key in COUNTERS
    )
    if selected != classified + errors or not blocked <= flagged <= classified:
        raise ValueError("Report classification counters are inconsistent")
    if forwarded + blocked > total or selected > total:
        raise ValueError("Report admission counters are inconsistent")
    if report["enforcement_mode"] == "monitor":
        if blocked:
            raise ValueError("Monitor reports cannot contain blocked requests")
    elif report["enforcement_mode"] == "block":
        if selected != total:
            raise ValueError("Block reports must select every admitted request")
    else:
        raise ValueError("Invalid enforcement mode")


@dataclass(eq=False)
class Ticket:
    """Counter bookkeeping only: deliberately no request ID, body or user identity."""

    period: _Period
    deadline: float
    selected: bool
    classification_done: bool = False
    forwarded: bool = False
    blocked: bool = False


@dataclass
class _Period:
    index: int
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(COUNTERS, 0))
    pending: set[Ticket] = field(default_factory=set)
    finalized: bool = False


class Reporter:
    """Event-loop-confined accounting; all mutations are synchronous and atomic."""

    def __init__(
        self,
        config: Config,
        classifier: Classifier,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_time: datetime | None = None,
    ) -> None:
        self.config = config
        self.classifier_id = classifier.id
        self.clock = clock
        self._started = clock()
        self._wall_started = wall_time or datetime.now(UTC)
        self.boot_id = str(uuid.uuid4())
        self.signing_key = SigningKey()
        self._manifest_json = canonical_json(build_manifest(config, classifier))
        self.policy_digest = hashlib.sha256(self._manifest_json.encode("utf-8")).hexdigest()
        self._periods: dict[int, _Period] = {}
        self._next_period = 0
        self._history: deque[dict[str, str]] = deque(maxlen=config.report_history)

    @property
    def manifest(self) -> dict[str, Any]:
        return json.loads(self._manifest_json)

    @property
    def public_identity(self) -> dict[str, str]:
        return {
            "public_key": self.signing_key.public_key,
            "aiab_id": self.config.aiab_id,
            "boot_id": self.boot_id,
            "policy_digest": self.policy_digest,
        }

    def admit(self, *, selected: bool) -> Ticket:
        self.finalize_due()
        now = self.clock()
        index = int((now - self._started) // self.config.report_period_seconds)
        period = self._periods.setdefault(index, _Period(index))
        period.counts["requests_total"] += 1
        ticket = Ticket(period, now + self.config.processing_timeout_seconds, selected)
        if selected:
            period.counts["requests_selected"] += 1
            period.pending.add(ticket)
        return ticket

    def live(self, ticket: Ticket) -> bool:
        return not ticket.period.finalized and self.clock() < ticket.deadline

    def forwarded(self, ticket: Ticket) -> bool:
        if not self.live(ticket) or ticket.forwarded or ticket.blocked:
            return False
        ticket.forwarded = True
        ticket.period.counts["requests_forwarded"] += 1
        return True

    def classified(self, ticket: Ticket, result: ClassifyResult, *, block: bool = False) -> bool:
        if not self.live(ticket):
            self.error(ticket)
            return False
        if not ticket.selected or ticket.classification_done:
            return False
        ticket.classification_done = True
        ticket.period.pending.discard(ticket)
        ticket.period.counts["requests_classified"] += 1
        ticket.period.counts["requests_flagged"] += int(result.flagged)
        if block:
            if not result.flagged or ticket.forwarded:
                raise ValueError("Invalid blocking transition")
            ticket.blocked = True
            ticket.period.counts["requests_blocked"] += 1
        return True

    def error(self, ticket: Ticket) -> None:
        if ticket.selected and not ticket.classification_done and not ticket.period.finalized:
            ticket.classification_done = True
            ticket.period.pending.discard(ticket)
            ticket.period.counts["classifier_errors"] += 1

    def finalize_due(self) -> None:
        now = self.clock()
        # Resolve individual deadlines as well as closed reporting periods.
        for period in self._periods.values():
            for ticket in tuple(period.pending):
                if now >= ticket.deadline:
                    self.error(ticket)
        duration = self.config.report_period_seconds
        while now >= self._started + (self._next_period + 1) * duration + (
            self.config.processing_timeout_seconds
        ):
            index = self._next_period
            period = self._periods.pop(index, _Period(index))
            for ticket in tuple(period.pending):
                self.error(ticket)
            period.finalized = True
            start = self._wall_started + timedelta(seconds=index * duration)
            end = start + timedelta(seconds=duration)
            report = {
                "schema": "aiab-report/v1",
                "aiab_id": self.config.aiab_id,
                "boot_id": self.boot_id,
                "sequence": index + 1,
                "policy_digest": self.policy_digest,
                "classifier_id": self.classifier_id,
                "enforcement_mode": self.config.enforcement_mode,
                "period_start": start.isoformat().replace("+00:00", "Z"),
                "period_end": end.isoformat().replace("+00:00", "Z"),
                **period.counts,
            }
            validate_counters(report)
            encoded = canonical_json(report)
            self._history.append({"report": encoded, "signature": self.signing_key.sign(encoded)})
            self._next_period += 1

    @property
    def reports(self) -> list[dict[str, str]]:
        self.finalize_due()
        return [dict(envelope) for envelope in self._history]
