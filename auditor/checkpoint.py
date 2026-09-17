"""Small auditor-local continuity hints; no reports or conversation bodies are retained."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from aiab.reporter import canonical_json
from auditor.verify import (
    IDENTITY_FIELDS,
    VerificationError,
    VerifiedReports,
    strict_json,
    timestamp,
    validate_identity,
)

CHECKPOINT_FIELDS = IDENTITY_FIELDS | {"schema", "sequence", "period_end"}


def load_checkpoint(path: Path) -> dict[str, Any] | None:
    if path.is_symlink():
        raise VerificationError("Checkpoint must be a regular JSON file, not a symbolic link")
    if path.exists() and not path.is_file():
        raise VerificationError("Checkpoint must be a regular JSON file")
    try:
        with path.open("rb") as checkpoint:
            content = checkpoint.read(65537)
    except FileNotFoundError:
        return None
    if len(content) > 65536:
        raise VerificationError("Checkpoint exceeds the allowed size")
    value = strict_json(content)
    if not isinstance(value, dict) or value.keys() != CHECKPOINT_FIELDS:
        raise VerificationError("Invalid checkpoint schema")
    if value["schema"] != "aiab-checkpoint/v1":
        raise VerificationError("Unsupported checkpoint schema")
    validate_identity({key: value[key] for key in IDENTITY_FIELDS})
    if type(value["sequence"]) is not int or value["sequence"] < 1:
        raise VerificationError("Invalid checkpoint sequence")
    timestamp(value["period_end"])
    return value


def checkpoint_warnings(
    verified: VerifiedReports, previous: dict[str, Any] | None, *, now: datetime
) -> list[str]:
    warnings: list[str] = []
    identity = verified.identity
    if previous:
        for key, label in (
            ("aiab_id", "AIAB identity"),
            ("boot_id", "Boot"),
            ("public_key", "Signing key"),
            ("policy_digest", "Policy"),
        ):
            if identity[key] != previous[key]:
                warnings.append(f"{label} changed since checkpoint; continuity is unknown")
    if not verified.reports:
        warnings.append("No finalized reports fetched; report freshness and continuity are unknown")
        if previous and identity["boot_id"] == previous["boot_id"]:
            warnings.append("Latest report has receded to an empty history; checkpoint preserved")
        return warnings
    latest = verified.reports[-1]
    latest_end = timestamp(latest["period_end"])
    allowance = (
        verified.manifest["report_period_seconds"] + verified.manifest["processing_timeout_seconds"]
    )
    if (now - latest_end).total_seconds() > allowance:
        warnings.append("Latest report is overdue against the local clock and policy schedule")
    if previous and identity["boot_id"] == previous["boot_id"]:
        previous_end = timestamp(previous["period_end"])
        if latest["sequence"] < previous["sequence"] or latest_end < previous_end:
            warnings.append("Latest report has receded since checkpoint; checkpoint preserved")
        for report in verified.reports:
            if report["sequence"] == previous["sequence"]:
                if timestamp(report["period_end"]) != previous_end:
                    raise VerificationError("Checkpoint sequence has a conflicting period end")
        newer = [report for report in verified.reports if report["sequence"] > previous["sequence"]]
        if newer:
            first = newer[0]
            if first["sequence"] != previous["sequence"] + 1:
                warnings.append(
                    f"Missing sequences {previous['sequence'] + 1} through "
                    f"{first['sequence'] - 1} after checkpoint"
                )
            first_start = timestamp(first["period_start"])
            if first_start < previous_end:
                raise VerificationError("New reports overlap the checkpoint period")
            if first_start > previous_end:
                warnings.append("Time gap between checkpoint and new reports")
        if latest["sequence"] > previous["sequence"]:
            expected_elapsed = (latest["sequence"] - previous["sequence"]) * verified.manifest[
                "report_period_seconds"
            ]
            if (latest_end - previous_end).total_seconds() != expected_elapsed:
                warnings.append("Report period schedule differs from the previous checkpoint")
    return warnings


def save_checkpoint(path: Path, verified: VerifiedReports, previous: dict[str, Any] | None) -> None:
    if not verified.reports:
        return
    latest = verified.reports[-1]
    if previous and verified.identity["boot_id"] == previous["boot_id"]:
        if latest["sequence"] < previous["sequence"] or timestamp(latest["period_end"]) < timestamp(
            previous["period_end"]
        ):
            return
    checkpoint = {
        "schema": "aiab-checkpoint/v1",
        **verified.identity,
        "sequence": latest["sequence"],
        "period_end": latest["period_end"],
    }
    # Atomic replacement leaves an existing checkpoint intact if validation or writing fails.
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(canonical_json(checkpoint) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
