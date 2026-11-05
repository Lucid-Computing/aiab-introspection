"""Lossless imports; no network, path loading, WORM or Amber proof assumptions.

``ingest_records(source, records, *, dataset_id, universe, ...)`` accepts native
JSON objects or UTF-8 JSON bytes. Byte inputs retain their exact encoding; object
inputs retain canonical JSON with that distinction recorded. The independent
``universe`` is a collector/inventory assertion, never inferred from records.
NativeVerificationAdapter is supplied by a source owner with its real trust policy.
Its result is an assertion, not an inference that hashing proves completeness.

``import_experiment_json`` accepts a normalized Dataset JSON document, preserving
its supplied manifest and bytes. Synthetic signatures are ordinary Ed25519 over a
framed source commitment, explicitly demo-only, not a proposed native proof format.
"""

import base64
import copy
import hashlib
import json
from collections.abc import Iterable
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .contracts import canonical_json, source_digest, timestamp, validate_dataset

MAX_IMPORT_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 1_000_000
SOURCES = {"agent", "proxy", "slack", "github", "experiment"}


class NativeVerificationAdapter(Protocol):
    """Source-specific verifier; must validate origin, checkpoints and key epochs.

    Run on all original bytes BEFORE normalization/filtering. Return JSON with
    ``authenticity`` and ``coverage`` assertions, including limitations. Raise
    ValueError on invalid signatures, forks, replay, gaps or unsupported proofs.
    No native implementation is bundled until owner fixtures and trust specs exist.
    """

    def verify(self, records: tuple[bytes, ...], manifest: dict) -> dict: ...


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _decode(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_IMPORT_BYTES:
        raise ValueError("import_size_limit")
    try:
        return json.loads(
            raw,
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid_number")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid_native_json") from exc


def _actor(value):
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, dict):
        # Native stable IDs only. Never resolve a display name/login into a person.
        return str(value["id"]) if value.get("id") is not None else None
    raise ValueError("invalid_actor")


def _time(value, *, slack=False):
    if value is None:
        return None
    if slack:
        # Slack's decimal timestamp remains preserved as native_id and raw bytes.
        from datetime import UTC, datetime
        from decimal import Decimal, InvalidOperation

        try:
            precise_micros = Decimal(str(value)) * 1_000_000
            micros = int(precise_micros)
            if precise_micros != micros:
                raise ValueError("submicrosecond_timestamp_not_supported")
            from datetime import timedelta

            return (datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=micros)).isoformat()
        except (ValueError, OverflowError, InvalidOperation) as exc:
            raise ValueError("invalid_slack_timestamp") from exc
    timestamp(value)
    return value


def _normalize(source, record, position, raw_hash):
    native_id = record.get("native_id", record.get("id"))
    if native_id is None and source == "slack":
        native_id = record.get("ts")
    if native_id is None and source in {"agent", "proxy"}:
        if all(k in record for k in ["stream", "batch", "offset"]):
            native_id = f"{record['stream']}:{record['batch']}:{record['offset']}"
    if native_id is None:
        raise ValueError("missing_native_id")
    if source == "github":
        if not record.get("repo"):
            raise ValueError("github_repository_required")
        native_id = f"{record['repo']}:{record.get('kind', 'record')}:{native_id}"
    if source == "slack":
        # Message ts is unique only within its channel.
        channel = record.get("channel_id", record.get("channel"))
        if not channel:
            raise ValueError("slack_channel_required")
        native_id = f"{channel}:{native_id}"
    occurred = record.get(
        "occurred_at", record.get("timestamp", record.get("created_at", record.get("time")))
    )
    if source == "slack" and occurred is None:
        occurred = _time(record.get("ts"), slack=True)
    elif source == "proxy" and isinstance(occurred, (int, float)):
        occurred = _time(occurred, slack=True)
    else:
        occurred = _time(
            occurred
            if occurred is not None
            else (record.get("ts") if source in {"agent", "proxy"} else None)
        )
    content = record.get("content", record.get("text", record.get("body")))
    if content is not None and not isinstance(content, str):
        # Structured tool calls/results are preserved, without lossy str(dict).
        content = canonical_json(content).decode()
    if (
        content is None
        and source == "proxy"
        and any(k in record for k in ["request_raw", "response_raw"])
    ):
        content = canonical_json(
            {k: record[k] for k in ["request_raw", "response_raw"] if k in record}
        ).decode()
    actor = record.get("actor", record.get("user"))
    if actor is None and source == "github" and isinstance(record.get("author"), dict):
        actor = record["author"]
    attributes = record.get("attributes", {})
    if not isinstance(attributes, dict):
        raise ValueError("invalid_native_attributes")
    loss = record.get("loss", [])
    if record.get("truncated") and not loss:
        raise ValueError("truncation_requires_loss_flag")
    return {
        "id": f"{source}:{native_id}",
        "source": source,
        "native_id": str(native_id),
        "actor": _actor(actor),
        "agent_id": record.get("agent_id"),
        "session_id": record.get("session_id"),
        "campaign_id": record.get("campaign_id"),
        "project_id": record.get("project_id"),
        "event_type": record.get(
            "event_type", record.get("type", record.get("kind", f"{source}.record"))
        ),
        "occurred_at": occurred,
        "ingested_at": record.get("ingested_at"),
        "artifact_ids": record.get("artifact_ids", []),
        "content": content,
        # Only explicitly normalized attributes are copied; other metadata stays native.
        "attributes": {
            **copy.deepcopy(attributes),
            "clock_quality": record.get("clock_quality", "source-asserted"),
            "truncated": bool(record.get("truncated")),
        },
        "classification": record.get("classification", "internal"),
        "provenance": {
            "raw_sha256": raw_hash,
            "adapter": f"{source}-json-v1",
            "native_position": position,
            "normalization_version": 1,
            "loss": loss,
        },
    }


def ingest_records(
    source: str,
    records: Iterable[bytes | dict],
    *,
    dataset_id: str,
    universe: dict,
    title: str | None = None,
    origin="imported",
    collection: dict | None = None,
    verifier: NativeVerificationAdapter | None = None,
):
    if source not in SOURCES:
        raise ValueError("unsupported_source")
    if not isinstance(universe, dict) or not universe:
        raise ValueError("independent_universe_required")
    preserved, decoded, total = [], [], 0
    for index, record in enumerate(records):
        if index >= MAX_RECORDS:
            raise ValueError("record_limit")
        raw = record if isinstance(record, bytes) else canonical_json(record)
        total += len(raw)
        if total > MAX_IMPORT_BYTES:
            raise ValueError("import_size_limit")
        value = _decode(raw)
        if not isinstance(value, dict):
            raise ValueError("native_record_must_be_object")
        preserved.append(
            {
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes_base64": base64.b64encode(raw).decode(),
                "encoding": "original-json" if isinstance(record, bytes) else "canonical-json",
            }
        )
        decoded.append(value)
    descriptor = {
        "id": source,
        "connector_version": f"{source}-json-v1",
        "universe": copy.deepcopy(universe),
        "collection": copy.deepcopy(collection or {}),
        "record_hashes": [item["sha256"] for item in preserved],
    }
    verification = {
        "authenticity": "unverified",
        "coverage": "unverified",
        "limitations": ["Native source trust/checkpoint verification unavailable"],
    }
    if verifier is not None:
        verification = verifier.verify(
            tuple(base64.b64decode(p["bytes_base64"]) for p in preserved), copy.deepcopy(descriptor)
        )
        if (
            not isinstance(verification, dict)
            or not {"authenticity", "coverage"} <= verification.keys()
        ):
            raise ValueError("invalid_native_verification_result")
        canonical_json(verification)
    descriptor["verification"] = verification
    events = [
        _normalize(source, record, index, preserved[index]["sha256"])
        for index, record in enumerate(decoded)
    ]
    dataset = {
        "id": dataset_id,
        "title": title or dataset_id,
        "origin": origin,
        "manifest": {
            "sources": [descriptor],
            "coverage": {
                "universe": copy.deepcopy(universe),
                "status": "unverified",
                "limitations": ["Submitted record count does not establish completeness"],
            },
            "integrity": "bytes-committed",
            "source_digest": source_digest(descriptor),
        },
        "events": events,
        "native_records": preserved,
    }
    return validate_dataset(dataset)


def import_experiment_json(raw: bytes):
    dataset = _decode(raw)
    validate_dataset(dataset)
    dataset = copy.deepcopy(dataset)
    dataset["import_provenance"] = {
        "format": "normalized-experiment-json-v1",
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "bytes_base64": base64.b64encode(raw).decode(),
        "assurance": "Submitted JSON; source claims not independently verified",
    }
    return dataset


def sign_synthetic_snapshot(manifest: dict, key: Ed25519PrivateKey):
    payload = b"aiab-workbench/v1/synthetic-snapshot\0" + source_digest(manifest).encode()
    return {
        "kind": "synthetic-signed",
        "source_digest": source_digest(manifest),
        "signature": base64.b64encode(key.sign(payload)).decode(),
        "assurance": "Synthetic demo signature only; no native authenticity or completeness proof",
    }


def verify_synthetic_snapshot(manifest: dict, envelope: dict, key: Ed25519PublicKey):
    if envelope.get("kind") != "synthetic-signed" or envelope.get("source_digest") != source_digest(
        manifest
    ):
        raise ValueError("synthetic_commitment_mismatch")
    try:
        key.verify(
            base64.b64decode(envelope["signature"], validate=True),
            b"aiab-workbench/v1/synthetic-snapshot\0" + source_digest(manifest).encode(),
        )
    except (InvalidSignature, ValueError, KeyError, TypeError) as exc:
        raise ValueError("invalid_synthetic_signature") from exc
    return {
        "authenticity": "synthetic-signature-verified",
        "native_verified": False,
        "completeness_verified": False,
    }


def ingest_agent(records, **kwargs):
    """Import stable native agent events; kwargs are those of ingest_records."""
    return ingest_records("agent", records, **kwargs)


def ingest_proxy(records, **kwargs):
    """Import raw proxy JSON; unsigned proxy payloads remain unverified."""
    return ingest_records("proxy", records, **kwargs)


def ingest_slack(records, **kwargs):
    """Import Slack messages, keyed by channel ID and exact message timestamp."""
    return ingest_records("slack", records, **kwargs)


def ingest_github(records, **kwargs):
    """Import repo/kind-scoped GitHub records; logins are not person identities."""
    return ingest_records("github", records, **kwargs)


def assemble_corpus(
    snapshots: Iterable[dict], *, dataset_id: str, title: str, universe: dict, origin="imported"
):
    """Commit partitions into one queryable corpus, retaining source-native bytes.

    The independent inventory is supplied separately. Partition commitments bind
    connector metadata/checkpoints as well as the normalized events, so cross-
    partition joins run against one store rather than unrelated classification jobs.
    Identical/conflicting duplicate event IDs are rejected explicitly by validation;
    collection gaps and missing partitions stay unverified until a native verifier
    reconciles them against the independent inventory.
    """
    from .contracts import dataset_digest

    if not isinstance(universe, dict) or not universe:
        raise ValueError("independent_universe_required")
    events, native_records, partitions, sources = [], {}, [], {}
    total = 0
    for snapshot in snapshots:
        validate_dataset(snapshot)
        commitment = dataset_digest(snapshot)
        if any(p["digest"] == commitment for p in partitions):
            raise ValueError("duplicate_partition")
        partitions.append(
            {
                "id": snapshot["id"],
                "digest": commitment,
                "manifest": copy.deepcopy(snapshot["manifest"]),
            }
        )
        for event in snapshot["events"]:
            total += 1
            if total > MAX_RECORDS:
                raise ValueError("record_limit")
            events.append(copy.deepcopy(event))
        for record in snapshot.get("native_records", []):
            native_records[record["sha256"]] = copy.deepcopy(record)
        for descriptor in snapshot["manifest"]["sources"]:
            identifier = descriptor if isinstance(descriptor, str) else descriptor["id"]
            merged = sources.setdefault(
                identifier, {"id": identifier, "partitions": [], "record_hashes": []}
            )
            merged["partitions"].append(
                {"dataset_digest": commitment, "source": copy.deepcopy(descriptor)}
            )
            if isinstance(descriptor, dict):
                merged["record_hashes"].extend(descriptor.get("record_hashes", []))
    dataset = {
        "id": dataset_id,
        "title": title,
        "origin": origin,
        "manifest": {
            "sources": list(sources.values()),
            "partitions": partitions,
            "coverage": {"universe": copy.deepcopy(universe), "status": "unverified"},
            "integrity": "partition-committed",
        },
        "events": events,
    }
    if native_records:
        dataset["native_records"] = list(native_records.values())
    return validate_dataset(dataset)


def validate_native_bindings(dataset):
    """Recompute bundled adapter transformations before trusting native assertions.

    A native byte signature cannot authenticate a caller-modified normalized actor,
    source or content. Unknown normalizers require a source-owner implementation;
    this function deliberately refuses to upgrade their assurance.
    """
    validate_dataset(dataset)
    raw_index = {r["sha256"]: r for r in dataset.get("native_records", [])}
    descriptors = {s["id"]: s for s in dataset["manifest"]["sources"] if isinstance(s, dict)}
    observed = {}
    for event in dataset["events"]:
        provenance = event["provenance"]
        if provenance.get("adapter") != f"{event['source']}-json-v1":
            raise ValueError("unsupported_native_normalization")
        raw_hash = provenance.get("raw_sha256")
        if raw_hash not in raw_index:
            raise ValueError("native_snapshot_missing")
        observed.setdefault(event["source"], set()).add(raw_hash)
        raw = base64.b64decode(raw_index[raw_hash]["bytes_base64"], validate=True)
        reconstructed = _normalize(
            event["source"], _decode(raw), provenance.get("native_position"), raw_hash
        )
        if reconstructed != event:
            raise ValueError("normalized_native_mismatch")
    for source, hashes in observed.items():
        if source not in descriptors or hashes != set(descriptors[source].get("record_hashes", [])):
            raise ValueError("source_normalization_coverage_gap")
    return dataset
