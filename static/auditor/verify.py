"""Validate canonical report signatures, artifact linkage, counters, and period ordering."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from aiab.reporter import REPORT_FIELDS, canonical_json, validate_counters

IDENTITY_FIELDS = {"aiab_id", "boot_id", "public_key", "policy_digest"}
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")


class VerificationError(ValueError):
    """Untrusted artifacts failed validation; messages never contain their contents."""


def strict_json(value: str | bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise VerificationError("JSON contains a duplicate field")
            result[key] = item
        return result

    def constant(_: str) -> None:
        raise VerificationError("JSON contains a non-finite number")

    def floating(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise VerificationError("JSON contains a non-finite number")
        return number

    try:
        return json.loads(
            value, object_pairs_hook=pairs, parse_constant=constant, parse_float=floating
        )
    except (ValueError, UnicodeError, RecursionError):
        raise VerificationError("Invalid JSON artifact") from None


def timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise VerificationError("Invalid RFC3339 period timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise VerificationError("Invalid RFC3339 period timestamp") from None


def _text(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 256 and value.isprintable()


def _digest(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def decode_base64(value: Any, length: int) -> bytes:
    try:
        if not isinstance(value, str):
            raise ValueError
        decoded = base64.b64decode(value, validate=True)
        if len(decoded) != length or base64.b64encode(decoded).decode("ascii") != value:
            raise ValueError
        return decoded
    except (ValueError, binascii.Error):
        raise VerificationError("Invalid public key or signature encoding") from None


def validate_identity(identity: Any) -> dict[str, str]:
    if not isinstance(identity, dict) or identity.keys() != IDENTITY_FIELDS:
        raise VerificationError("Invalid public identity schema")
    if not _text(identity["aiab_id"]) or not _text(identity["boot_id"]):
        raise VerificationError("Invalid AIAB or boot identifier")
    if not _digest(identity["policy_digest"]):
        raise VerificationError("Invalid policy digest")
    decode_base64(identity["public_key"], 32)
    return identity


def validate_manifest(
    manifest: Any, identity: dict[str, str], expected_policy_digest: str | None = None
) -> dict[str, Any]:
    if not isinstance(manifest, dict) or manifest.get("schema") != "aiab-manifest/v1":
        raise VerificationError("Invalid policy manifest schema")
    if not _text(manifest.get("classifier_id")) or manifest.get("enforcement_mode") not in (
        "monitor",
        "block",
    ):
        raise VerificationError("Invalid manifest classifier or enforcement mode")
    period = manifest.get("report_period_seconds")
    timeout = manifest.get("processing_timeout_seconds")
    if type(period) is not int or period < 1:
        raise VerificationError("Invalid manifest reporting period")
    if (
        type(timeout) not in (float, int)
        or timeout < 0
        or (isinstance(timeout, float) and not math.isfinite(timeout))
    ):
        raise VerificationError("Invalid manifest processing timeout")
    try:
        digest = hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
    except (ValueError, TypeError, RecursionError):
        raise VerificationError("Invalid policy manifest") from None
    if digest != identity["policy_digest"]:
        raise VerificationError("Manifest and public key policy digest mismatch; fetch again")
    if expected_policy_digest is not None and digest != expected_policy_digest:
        raise VerificationError("Policy digest does not match independently approved digest")
    return manifest


@dataclass(frozen=True)
class VerifiedReports:
    identity: dict[str, str]
    manifest: dict[str, Any]
    reports: list[dict[str, Any]]
    warnings: list[str]


def verify_reports(
    identity: Any,
    manifest: Any,
    envelopes: Any,
    *,
    expected_policy_digest: str | None = None,
) -> VerifiedReports:
    identity = validate_identity(identity)
    manifest = validate_manifest(manifest, identity, expected_policy_digest)
    if not isinstance(envelopes, list):
        raise VerificationError("Reports endpoint must contain a list")
    public_key = Ed25519PublicKey.from_public_bytes(decode_base64(identity["public_key"], 32))
    reports: list[dict[str, Any]] = []
    seen: dict[int, str] = {}
    warnings: list[str] = []
    previous_end: datetime | None = None
    for envelope in envelopes:
        if not isinstance(envelope, dict) or envelope.keys() != {"report", "signature"}:
            raise VerificationError("Invalid signed report envelope")
        serialized = envelope["report"]
        if not isinstance(serialized, str):
            raise VerificationError("Signed report must be a JSON string")
        try:
            public_key.verify(decode_base64(envelope["signature"], 64), serialized.encode("utf-8"))
        except (InvalidSignature, UnicodeError):
            raise VerificationError("Report signature verification failed") from None
        report = strict_json(serialized)
        if not isinstance(report, dict) or report.keys() != REPORT_FIELDS:
            raise VerificationError("Report must contain exactly the aiab-report/v1 fields")
        if report["schema"] != "aiab-report/v1" or canonical_json(report) != serialized:
            raise VerificationError("Invalid report schema or noncanonical serialization")
        for key in ("aiab_id", "boot_id", "policy_digest"):
            if report[key] != identity[key]:
                raise VerificationError("Report identity or policy mismatch; fetch again")
        for key in ("classifier_id", "enforcement_mode"):
            if report[key] != manifest[key]:
                raise VerificationError("Report classifier or enforcement mode differs from policy")
        sequence = report["sequence"]
        if type(sequence) is not int or sequence < 1:
            raise VerificationError("Report sequence must be a positive integer")
        try:
            validate_counters(report)
        except ValueError:
            raise VerificationError("Report counter invariants failed") from None
        start, end = timestamp(report["period_start"]), timestamp(report["period_end"])
        if end <= start or (end - start).total_seconds() != manifest["report_period_seconds"]:
            raise VerificationError("Report period does not match policy duration")
        if sequence in seen:
            if seen[sequence] != serialized:
                raise VerificationError("Conflicting reports for the same boot sequence")
            continue
        if reports:
            previous_sequence = reports[-1]["sequence"]
            if sequence <= previous_sequence or start < previous_end:
                raise VerificationError("Report sequence or time ordering is invalid")
            if sequence != previous_sequence + 1:
                warnings.append(f"Missing sequences {previous_sequence + 1} through {sequence - 1}")
            if start > previous_end:
                warnings.append(f"Time gap before sequence {sequence}")
            elif sequence != previous_sequence + 1:
                raise VerificationError("Missing sequence has no corresponding period time gap")
        elif sequence > 1:
            warnings.append(
                f"Fetched history starts at sequence {sequence}; earlier reports missing"
            )
        seen[sequence] = serialized
        reports.append(report)
        previous_end = end
    return VerifiedReports(identity, manifest, reports, warnings)
