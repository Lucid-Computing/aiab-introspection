import base64
import copy
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aiab.workbench.contracts import validate_dataset
from aiab.workbench.ingest import (
    import_experiment_json,
    ingest_agent,
    ingest_github,
    ingest_proxy,
    ingest_records,
    ingest_slack,
    sign_synthetic_snapshot,
    verify_synthetic_snapshot,
)

KWARGS = {"dataset_id": "d", "universe": {"accounts": ["alice"], "inventory": "independent"}}


@pytest.mark.parametrize(
    "adapter,record",
    [
        (ingest_agent, {"id": "1", "actor": "alice", "content": "tool result", "opaque": [1, 2]}),
        (
            ingest_proxy,
            {
                "id": "1",
                "time": 1790000000.25,
                "request_raw": {"text": "complete request"},
                "response_raw": {"text": "complete SSE and tool calls"},
            },
        ),
        (
            ingest_slack,
            {
                "channel_id": "C1",
                "channel": "general",
                "ts": "1790000000.123456",
                "user": "U1",
                "text": "message",
                "thread_ts": "1789999999.000001",
                "reactions": [{"name": "eyes", "users": ["U2"]}],
            },
        ),
        (
            ingest_github,
            {
                "id": 1,
                "repo": "org/repo",
                "kind": "review",
                "author": {"id": 9},
                "time": "2026-10-01T10:00:00Z",
                "text": "review",
                "sha": "commit",
            },
        ),
    ],
)
def test_native_bytes_metadata_survive(adapter, record):
    raw = json.dumps(record, indent=2, ensure_ascii=False).encode() + b"\n"
    dataset = adapter([raw], **KWARGS)
    assert base64.b64decode(dataset["native_records"][0]["bytes_base64"]) == raw
    assert dataset["native_records"][0]["encoding"] == "original-json"
    assert dataset["manifest"]["sources"][0]["verification"]["authenticity"] == "unverified"
    assert dataset["manifest"]["coverage"]["status"] == "unverified"
    assert dataset["events"][0]["provenance"]["native_position"] == 0
    assert record == json.loads(raw)


def test_proxy_existing_record_shape_and_structured_content():
    dataset = ingest_proxy(
        [
            {
                "stream": "s",
                "batch": 7,
                "offset": 2,
                "kind": "response",
                "ts": "2026-01-01T00:00:00Z",
                "content": {"tool_calls": [1]},
            }
        ],
        **KWARGS,
    )
    event = dataset["events"][0]
    assert event["native_id"] == "s:7:2"
    assert event["occurred_at"] == "2026-01-01T00:00:00Z"
    assert json.loads(event["content"]) == {"tool_calls": [1]}
    assert event["actor"] is None


def test_identity_and_clock_not_invented():
    dataset = ingest_github([{"id": 1, "repo": "r", "author": "alice", "text": "x"}], **KWARGS)
    event = dataset["events"][0]
    assert event["actor"] is None
    assert event["occurred_at"] is None
    assert event["ingested_at"] is None
    slack = ingest_slack([{"channel": "C1", "ts": "1790000000.123456"}], **KWARGS)
    assert slack["events"][0]["occurred_at"].endswith(".123456+00:00")


@pytest.mark.parametrize("raw", [b'{"id":1,"id":2}', b'{"id": NaN}', b'{"id":', b"\xff", b"[]"])
def test_invalid_json_not_silently_dropped(raw):
    with pytest.raises(ValueError):
        ingest_agent([raw], **KWARGS)


def test_universe_empty_and_duplicate_conflict():
    with pytest.raises(ValueError, match="universe"):
        ingest_agent([], dataset_id="d", universe={})
    with pytest.raises(ValueError, match="duplicate"):
        ingest_agent([{"id": "1", "content": "a"}, {"id": "1", "content": "b"}], **KWARGS)
    empty = ingest_agent([], **KWARGS)
    assert empty["manifest"]["sources"][0]["universe"] == KWARGS["universe"]
    assert not empty["events"]


def test_native_reference_tampering_and_normalized_import():
    dataset = ingest_agent([{"id": "1"}], **KWARGS)
    raw = json.dumps(dataset).encode()
    imported = import_experiment_json(raw)
    assert imported["events"] == dataset["events"]
    assert base64.b64decode(imported["import_provenance"]["bytes_base64"]) == raw
    broken = copy.deepcopy(dataset)
    broken["native_records"][0]["bytes_base64"] = base64.b64encode(b"tampered").decode()
    with pytest.raises(ValueError, match="invalid_native_reference"):
        validate_dataset(broken)
    dataset["native_records"] = []
    with pytest.raises(ValueError, match="native_reference_missing"):
        validate_dataset(dataset)


def test_native_adapter_sees_original_full_snapshot_before_normalization():
    class Verifier:
        def verify(self, records, manifest):
            assert records == (b'{"id":"1"}', b'{"id":"2"}')
            assert manifest["universe"] == KWARGS["universe"]
            return {"authenticity": "verified", "coverage": "limited", "limitations": ["gap"]}

    dataset = ingest_records("agent", [b'{"id":"1"}', b'{"id":"2"}'], verifier=Verifier(), **KWARGS)
    assert dataset["manifest"]["coverage"]["status"] != "complete"


def test_native_adapter_invalid_and_no_invented_sources():
    class Invalid:
        def verify(self, records, manifest):
            raise ValueError("bad_native_signature")

    with pytest.raises(ValueError, match="bad_native_signature"):
        ingest_agent([{"id": "1"}], verifier=Invalid(), **KWARGS)
    with pytest.raises(ValueError, match="unsupported_source"):
        ingest_records("worm", [], **KWARGS)


def test_synthetic_signatures_are_not_native_proofs():
    key = Ed25519PrivateKey.generate()
    manifest = {"sources": ["synthetic"], "universe": {"accounts": ["alice"]}}
    envelope = sign_synthetic_snapshot(manifest, key)
    result = verify_synthetic_snapshot(manifest, envelope, key.public_key())
    assert not result["native_verified"]
    assert not result["completeness_verified"]
    with pytest.raises(ValueError):
        verify_synthetic_snapshot(manifest, envelope, Ed25519PrivateKey.generate().public_key())
    with pytest.raises(ValueError):
        verify_synthetic_snapshot({"changed": True}, envelope, key.public_key())


def test_manifest_partition_corpus_joins_and_missing_source_coverage():
    from aiab.workbench.contracts import EVENT_FIELDS
    from aiab.workbench.evidence import EvidenceStore
    from aiab.workbench.ingest import assemble_corpus

    left = ingest_agent(
        [{"id": "1", "actor": "alice", "session_id": "shared"}],
        dataset_id="partition-1",
        universe={"accounts": ["alice"]},
    )
    right = ingest_agent(
        [{"id": "2", "actor": "alice", "session_id": "shared"}],
        dataset_id="partition-2",
        universe={"accounts": ["alice"]},
    )
    corpus = assemble_corpus(
        [left, right],
        dataset_id="whole",
        title="Whole snapshot",
        universe={"expected_partitions": ["partition-1", "partition-2", "p3"]},
    )
    assert len(corpus["manifest"]["partitions"]) == 2
    assert len(corpus["manifest"]["sources"]) == 1
    scope = {
        "dataset_ids": ["whole"],
        "subjects": ["alice"],
        "required_sources": ["agent"],
        "fields": EVENT_FIELDS.copy(),
        "time_start": None,
        "time_end": None,
    }
    store = EvidenceStore(corpus, scope, allow_native=True)
    assert len(store.join(["agent:1"], ["agent:2"], on="session_id")) == 1
    assert store.query_sql("SELECT count(*) AS n FROM events") == [{"n": 2}]
    assert json.loads(store.fetch_native("agent:2"))["id"] == "2"
    assert not store.coverage()["negative_claim_permitted"]
    with pytest.raises(ValueError, match="duplicate_partition"):
        assemble_corpus([left, left], dataset_id="dup", title="Dup", universe={"x": 1})


def test_explicit_attributes_survive_and_bad_clock_is_not_silently_unknown():
    dataset = ingest_agent(
        [{"id": "1", "attributes": {"target": "sandbox", "cost": 0.5}}], **KWARGS
    )
    assert dataset["events"][0]["attributes"]["target"] == "sandbox"
    with pytest.raises(ValueError):
        ingest_agent([{"id": "1", "occurred_at": ""}], **KWARGS)
    with pytest.raises(ValueError):
        ingest_slack([{"channel": "C", "ts": "1790000000.1234567"}], **KWARGS)
