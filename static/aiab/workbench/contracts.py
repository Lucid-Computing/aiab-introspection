"""JSON contracts and RFC8785 commitments for the independent workbench API.

Extensions are committed, never stripped. Operator-specific executable config validation
belongs to the operator at admission. These structural checks do not authorize execution.
"""

import base64
import hashlib
import re
from datetime import datetime

import rfc8785
from jsonschema import Draft202012Validator

VERSION = "aiab-workbench/v1"
KINDS = ["select", "join", "aggregate", "rules", "sql", "python", "agent", "assert", "release"]
EVENT_FIELDS = (
    "id source native_id actor agent_id session_id campaign_id project_id event_type "
    "occurred_at ingested_at artifact_ids content attributes classification provenance"
).split()
S = {"type": "string", "minLength": 1}
STRINGS = {"type": "array", "items": S, "uniqueItems": True}
NULL_STRING = {"type": ["string", "null"]}


def obj(properties, required=None):
    return {"type": "object", "properties": properties, "required": required or list(properties)}


SCOPE_SCHEMA = obj(
    {
        "dataset_ids": STRINGS,
        "subjects": STRINGS,
        "fields": STRINGS,
        "required_sources": STRINGS,
        "time_start": NULL_STRING,
        "time_end": NULL_STRING,
    }
)
PLAN_SCHEMA = obj(
    {
        "version": {"const": VERSION},
        "id": S,
        "title": S,
        "question": S,
        "criteria": {"type": "array", "minItems": 1, "items": obj({"id": S, "description": S})},
        "mode": {"enum": ["personal", "joint", "confidential"]},
        "scope": SCOPE_SCHEMA,
        "steps": {
            "type": "array",
            "minItems": 1,
            "items": obj(
                {
                    "id": S,
                    "kind": {"enum": KINDS},
                    "title": S,
                    "inputs": STRINGS,
                    "config": {"type": "object"},
                }
            ),
        },
        "agent": obj(
            {
                "provider": {"const": "tinfoil"},
                "model": S,
                **{
                    k: {"type": "integer", "minimum": 1}
                    for k in ["max_turns", "max_tokens", "max_tool_calls", "max_seconds"]
                },
            }
        ),
        "disclosure": obj(
            {
                "recipients": {
                    "type": "array",
                    "minItems": 1,
                    "items": obj(
                        {
                            "id": S,
                            "role": S,
                            "fields": STRINGS,
                            "allow_evidence": {"type": "boolean"},
                            "allow_excerpts": {"type": "boolean"},
                        }
                    ),
                },
                "min_group_size": {"type": "integer", "minimum": 1},
                "max_releases": {"type": "integer", "minimum": 1},
            }
        ),
        "approval": obj(
            {"required_signers": STRINGS, "roster": {"type": "array"}, "expires_at": NULL_STRING}
        ),
    }
)
EVENT_SCHEMA = obj(
    {
        **{k: S for k in ["id", "source", "native_id", "event_type", "classification"]},
        **{
            k: NULL_STRING
            for k in [
                "actor",
                "agent_id",
                "session_id",
                "campaign_id",
                "project_id",
                "occurred_at",
                "ingested_at",
            ]
        },
        "artifact_ids": STRINGS,
        "content": {"type": ["string", "null"]},
        "attributes": {"type": "object"},
        "provenance": {"type": "object"},
    }
)
DATASET_SCHEMA = obj(
    {
        "id": S,
        "title": S,
        "origin": {"enum": ["synthetic", "native", "imported"]},
        "manifest": obj(
            {"sources": {"type": "array"}, "coverage": {"type": "object"}, "integrity": S}
        ),
        "events": {"type": "array", "items": EVENT_SCHEMA},
    }
)
FINDING_SCHEMA = obj(
    {
        "id": S,
        "criterion_id": S,
        "title": S,
        "claim": S,
        "outcome": {"enum": ["supported", "contradicted", "no_finding", "insufficient_evidence"]},
        "evidence_ids": STRINGS,
        "counterevidence_ids": STRINGS,
        "limitations": STRINGS,
        "method": S,
        "details": {"type": "object"},
    }
)


def timestamp(value):
    """Parse an explicit timezone; never guess chronology for an unknown timestamp."""
    if not isinstance(value, str):
        raise ValueError("invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid_timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp_requires_timezone")
    return parsed


def canonical_json(value):
    try:
        return rfc8785.dumps(value)
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise ValueError("noncanonical_json") from exc


def digest(domain, value):
    return hashlib.sha256(
        (VERSION + "/" + domain + "\0").encode() + canonical_json(value)
    ).hexdigest()


def _validate(schema, value):
    canonical_json(value)
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: str(e.path))
    if errors:
        raise ValueError(f"invalid_contract {list(errors[0].path)}: {errors[0].message}")
    return value


def validate_scope(scope):
    _validate(SCOPE_SCHEMA, scope)
    start, end = scope["time_start"], scope["time_end"]
    if start is not None:
        timestamp(start)
    if end is not None:
        timestamp(end)
    if start and end and timestamp(start) > timestamp(end):
        raise ValueError("reversed_time_scope")
    if set(scope["fields"]) - set(EVENT_FIELDS):
        raise ValueError("unknown_scope_fields")
    return scope


def _unique(items):
    ids = [item["id"] for item in items]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate_id")


def validate_plan(plan):
    _validate(PLAN_SCHEMA, plan)
    validate_scope(plan["scope"])
    for group in [plan["criteria"], plan["steps"], plan["disclosure"]["recipients"]]:
        _unique(group)
    seen = set()
    for step in plan["steps"]:
        if not set(step["inputs"]) <= seen:
            raise ValueError("step_inputs_must_precede_step")
        seen.add(step["id"])
    if plan["approval"]["expires_at"] is not None:
        timestamp(plan["approval"]["expires_at"])
    return plan


def validate_dataset(dataset):
    _validate(DATASET_SCHEMA, dataset)
    _unique(dataset["events"])
    source_ids = []
    for source in dataset["manifest"]["sources"]:
        source_id = (
            source
            if isinstance(source, str)
            else (source.get("id") if isinstance(source, dict) else None)
        )
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("invalid_source_descriptor")
        source_ids.append(source_id)
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("duplicate_source_descriptor")
    identities = dataset["manifest"].get("identities", [])
    _validate(
        {
            "type": "array",
            "items": obj(
                {
                    "subject": S,
                    "aliases": STRINGS,
                    "evidence_ids": STRINGS,
                    "uncertain": {"type": "boolean"},
                },
                ["subject", "aliases", "evidence_ids"],
            ),
        },
        identities,
    )
    native = set()
    native_records = dataset.get("native_records")
    raw_index = {}
    if native_records is not None:
        if not isinstance(native_records, list):
            raise ValueError("invalid_native_records")
        for record in native_records:
            try:
                raw = base64.b64decode(record["bytes_base64"], validate=True)
                raw_hash = hashlib.sha256(raw).hexdigest()
                if raw_hash != record["sha256"]:
                    raise ValueError("native_commitment_mismatch")
                raw_index[raw_hash] = raw
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("invalid_native_reference") from exc
    for event in dataset["events"]:
        key = (event["source"], event["native_id"])
        if key in native:
            raise ValueError("duplicate_native_conflict")
        native.add(key)
        for field in ["occurred_at", "ingested_at"]:
            if event[field] is not None:
                timestamp(event[field])
        raw_hash = event["provenance"].get("raw_sha256")
        if raw_hash is not None and (
            not isinstance(raw_hash, str) or not re.fullmatch("[0-9a-f]{64}", raw_hash)
        ):
            raise ValueError("invalid_raw_sha256")
        if native_records is not None and raw_hash not in raw_index:
            raise ValueError("native_reference_missing")
        if event["attributes"].get("truncated") and not event["provenance"].get("loss"):
            raise ValueError("truncation_requires_loss_flag")
    if native_records is not None:
        for source in dataset["manifest"]["sources"]:
            if isinstance(source, dict) and "record_hashes" in source:
                hashes = source["record_hashes"]
                if not isinstance(hashes, list) or any(
                    not isinstance(h, str) or h not in raw_index for h in hashes
                ):
                    raise ValueError("source_native_reference_missing")
        referenced = {e["provenance"].get("raw_sha256") for e in dataset["events"]}
        if set(raw_index) - referenced:
            raise ValueError("normalization_coverage_gap")
    return dataset


def validate_finding(finding):
    return _validate(FINDING_SCHEMA, finding)


def plan_digest(plan):
    return digest("plan", validate_plan(plan))


def source_digest(source):
    return digest("source", source)


def manifest_digest(manifest):
    return digest("manifest", manifest)


def dataset_digest(dataset):
    return digest("dataset", validate_dataset(dataset))
