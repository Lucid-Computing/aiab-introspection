"""Versioned record export evidence; native logger integration remains explicit."""

from datetime import UTC, datetime, timedelta

from aiab.audit.logger import bytes_digest, chain_head
from aiab.audit.protocol import (
    DATE,
    DIGEST,
    ID,
    AuditError,
    canonical,
    closed,
    digest,
    validate,
    verify_signature,
)
from aiab.baked.bundle import KINDS, SCOPE

RECORD = closed(
    {
        "stream": ID,
        "batch": {"type": "integer", "minimum": 0, "maximum": 2**53 - 1},
        "offset": {"type": "integer", "minimum": 0, "maximum": 2**53 - 1},
        "agent_id": ID,
        "session_id": ID,
        "ts": DATE,
        "kind": {"enum": KINDS},
        "content": {"type": "string", "maxLength": 1000000},
    }
)
SUBMISSION = closed(
    {
        "submission_id": ID,
        "plan_digest": DIGEST,
        "input_scope": SCOPE,
        "records": {"type": "array", "items": RECORD, "minItems": 1, "maxItems": 2000},
        "logger_evidence": {"type": "object"},
    }
)
STATEMENT = closed(
    {
        "schema": {"const": "aiab-record-export/v1"},
        "serialization": {"const": "aiab-records/v1"},
        "scope": SCOPE,
        "records_digest": DIGEST,
        "record_count": {"type": "integer", "minimum": 1, "maximum": 2000},
        "previous_head": DIGEST,
        "head": DIGEST,
        "first_sequence": {"type": "integer", "minimum": 0, "maximum": 2**53 - 1},
    }
)
EVIDENCE = closed({"statement": STATEMENT, "signature": {"type": "string", "maxLength": 128}})


def export_statement(records, scope, previous_head="sha256:" + "0" * 64, first_sequence=0):
    head = previous_head
    for i, record in enumerate(records):
        validate(record, RECORD, "invalid_records")
        head = chain_head(head, first_sequence + i, canonical(record))
    return {
        "schema": "aiab-record-export/v1",
        "serialization": "aiab-records/v1",
        "scope": scope,
        "records_digest": bytes_digest(canonical(records)),
        "record_count": len(records),
        "previous_head": previous_head,
        "head": head,
        "first_sequence": first_sequence,
    }


def scope_of(records):
    """The scope that covers exactly these records: their agents, first to last timestamp.

    Used by a logger (or the stand-in) when the plan carries no input_scope of its own."""
    times = sorted(datetime.fromisoformat(r["ts"]).astimezone(UTC) for r in records)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return {
        "logger_key_id": "logger",
        "agents": sorted({r["agent_id"] for r in records}),
        "window_start": times[0].strftime(fmt),
        "window_end": (times[-1] + timedelta(seconds=1)).strftime(fmt),
    }


def verify(submission, cfg, logger_key):
    records, scope, evidence = (
        submission[k] for k in ("records", "input_scope", "logger_evidence")
    )
    validate(evidence, EVIDENCE, "invalid_logger_evidence")
    statement = evidence["statement"]
    try:
        verify_signature(logger_key, evidence["signature"], "record-export", statement)
        expected = export_statement(
            records, scope, statement["previous_head"], statement["first_sequence"]
        )
        if statement != expected:
            raise AuditError("invalid_logger_evidence")
    except AuditError:
        raise AuditError("invalid_logger_evidence") from None
    start, end = (datetime.fromisoformat(scope[k]) for k in ("window_start", "window_end"))
    if start >= end or scope["logger_key_id"] != "logger":
        raise AuditError("scope_mismatch")
    parent = cfg.get("input_scope")  # optional: a plan may pin agents and a window
    if parent is not None and (
        not (
            datetime.fromisoformat(parent["window_start"])
            <= start
            < end
            <= datetime.fromisoformat(parent["window_end"])
        )
        or not set(scope["agents"]) <= set(parent["agents"])
    ):
        raise AuditError("scope_mismatch")
    positions = set()
    for record in records:
        if (
            record["agent_id"] not in scope["agents"]
            or not start <= datetime.fromisoformat(record["ts"]) < end
        ):
            raise AuditError("scope_mismatch")
        position = (record["stream"], record["batch"], record["offset"])
        if position in positions:
            raise AuditError("invalid_logger_evidence")
        positions.add(position)
    if (
        len(records) > cfg["limits"]["max_records_per_run"]
        or len(canonical(records)) > cfg["limits"]["max_input_utf8_bytes"]
    ):
        raise AuditError("input_too_large", 413)
    return digest(
        {
            "serialization": "aiab-records/v1",
            "scope": scope,
            "records_digest": statement["records_digest"],
            "statement_digest": digest(statement),
        }
    )
