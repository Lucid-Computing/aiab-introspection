import copy

import pytest

from aiab.workbench.contracts import EVENT_FIELDS
from aiab.workbench.evidence import EvidenceError, EvidenceStore
from aiab.workbench.ingest import ingest_agent


@pytest.fixture
def dataset():
    records = [
        {
            "id": "1",
            "actor": "alice",
            "occurred_at": "2026-10-01T10:00:00Z",
            "content": "hello 🌍\nline two",
            "session_id": "s",
            "secret": "native secret",
        },
        {
            "id": "2",
            "actor": "alice",
            "occurred_at": "2026-10-01T10:01:00Z",
            "content": "counterevidence",
            "session_id": "s",
        },
        {
            "id": "3",
            "actor": "bob",
            "occurred_at": "2026-10-01T10:00:00Z",
            "content": "bob forbidden",
            "session_id": "s",
        },
        {
            "id": "4",
            "actor": "alice",
            "occurred_at": "2026-11-01T10:00:00Z",
            "content": "outside period",
            "session_id": "s",
        },
        {"id": "5", "actor": "alice", "content": "unknown time"},
        {"id": "6", "content": "unknown actor"},
    ]
    return ingest_agent(records, dataset_id="d", universe={"accounts": ["alice", "bob"]})


@pytest.fixture
def scope():
    return {
        "dataset_ids": ["d"],
        "subjects": ["alice"],
        "required_sources": ["agent"],
        "fields": EVENT_FIELDS.copy(),
        "time_start": "2026-10-01T00:00:00Z",
        "time_end": "2026-10-02T00:00:00Z",
    }


@pytest.mark.parametrize("key", [None, "dataset_ids", "subjects", "fields", "required_sources"])
def test_empty_scope_never_authorizes(dataset, scope, key):
    if key is None:
        scope = None
    else:
        scope[key] = []
    with pytest.raises(EvidenceError, match="unresolved_authorized_scope"):
        EvidenceStore(dataset, scope)


def test_every_path_enforces_source_subject_time_fields(dataset, scope):
    scope["fields"] = ["id", "content", "session_id"]
    store = EvidenceStore(dataset, scope)
    assert {e["id"] for e in store.events} == {"agent:1", "agent:2"}
    assert store.search("forbidden") == []
    assert store.search("outside") == []
    assert store.search("native secret") == []
    assert store.search("unknown") == []
    assert store.query_sql("SELECT count(*) AS n FROM events") == [{"n": 2}]
    for ids in [["agent:3"], ["agent:4"], ["agent:5"], ["not real"]]:
        with pytest.raises(EvidenceError, match="reference_missing_or_unauthorized"):
            store.fetch(ids)
        with pytest.raises(EvidenceError):
            store.join(["agent:1"], ids, on="session_id")
    with pytest.raises(EvidenceError, match="field_not_authorized"):
        store.search("", actor="alice")
    with pytest.raises(EvidenceError):
        store.query_sql("SELECT actor FROM events")
    with pytest.raises(EvidenceError):
        store.query_sql("SELECT id FROM events WHERE actor = 'alice'")
    with pytest.raises(EvidenceError):
        store.temporal(["agent:1"], anchor="2026-10-01T10:00:00Z")
    with pytest.raises(EvidenceError):
        store.join(["agent:1"], ["agent:2"], on="actor")
    with pytest.raises(EvidenceError):
        store.fetch_native("agent:1")
    with pytest.raises(EvidenceError):
        store.resolve_identity("alice")
    scope["sources"] = ["slack"]
    assert EvidenceStore(dataset, scope).events == []


def test_defensive_copies_and_scope_cannot_expand(dataset, scope):
    store = EvidenceStore(dataset, scope)
    dataset["events"][0]["content"] = "modified"
    scope["subjects"].append("bob")
    fetched = store.fetch(["agent:1"])
    fetched[0]["content"] = "modified again"
    assert store.fetch(["agent:1"])[0]["content"] == "hello 🌍\nline two"
    assert store.search("forbidden") == []


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE events",
        "DELETE FROM events",
        "UPDATE events SET content='x'",
        "INSERT INTO events(id) VALUES ('x')",
        "ATTACH DATABASE '/tmp/escape.db' AS outside",
        "PRAGMA database_list",
        "VACUUM INTO '/tmp/escape.db'",
        "SELECT name FROM sqlite_master",
        "SELECT * FROM pragma_table_info('events')",
        "SELECT load_extension('/tmp/plugin')",
        "SELECT readfile('/etc/passwd')",
        "SELECT writefile('/tmp/a','b')",
        "SELECT randomblob(999999999)",
        "SELECT id FROM events; DELETE FROM events",
        "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) SELECT * FROM x",
        "BEGIN",
        "CREATE VIRTUAL TABLE secrets USING fts5(content)",
    ],
)
def test_hostile_sql_fails_closed(dataset, scope, sql):
    store = EvidenceStore(dataset, scope)
    with pytest.raises(EvidenceError):
        store.query_sql(sql)
    assert store.query_sql("SELECT count(*) AS n FROM events") == [{"n": 2}]


def test_sql_joins_aggregations_and_limits(dataset, scope):
    store = EvidenceStore(dataset, scope)
    assert store.query_sql("SELECT session_id, count(*) AS n FROM events GROUP BY session_id") == [
        {"session_id": "s", "n": 2}
    ]
    assert store.query_sql(
        "SELECT a.id AS a, b.id AS b FROM events a JOIN events b "
        "ON a.session_id=b.session_id WHERE a.id < b.id"
    ) == [{"a": "agent:1", "b": "agent:2"}]
    with pytest.raises(EvidenceError, match="duplicate_sql_columns"):
        store.query_sql("SELECT a.*, b.* FROM events a JOIN events b")
    tiny = EvidenceStore(dataset, scope, max_results=1)
    with pytest.raises(EvidenceError, match="result_limit"):
        tiny.query_sql("SELECT * FROM events")
    with pytest.raises(EvidenceError, match="result_limit"):
        tiny.search("")
    assert len(tiny.search("", limit=1)) == 1
    assert tiny.search("", limit=1, offset=1)[0]["id"] == "agent:2"
    assert tiny.query_sql("SELECT count(*) AS n FROM events") == [{"n": 2}]
    budget = EvidenceStore(dataset, scope, max_sql_steps=100)
    with pytest.raises(EvidenceError, match="sql_denied_or_resource_limit"):
        budget.query_sql(
            "SELECT count(*) FROM events a, events b, events c, events d, "
            "events e, events f, events g, events h, events i, events j"
        )


def test_unicode_lossless_spans_and_commitments(dataset, scope):
    store = EvidenceStore(dataset, scope)
    ref = store.reference("agent:1", start=6, end=7)
    assert store.fetch_reference(ref) == "🌍"
    broken = ref | {"event_digest": "0" * 64}
    with pytest.raises(EvidenceError, match="commitment"):
        store.fetch_reference(broken)
    with pytest.raises(EvidenceError, match="span"):
        store.reference("agent:1", start=-1)
    with pytest.raises(EvidenceError):
        store.fetch_reference(ref | {"event_id": "agent:3"})
    assert b"native secret" in EvidenceStore(dataset, scope, allow_native=True).fetch_native(
        "agent:1"
    )
    assert store.coverage()["examined_events"] == 1


def test_temporal_exact_join_and_unknown_chronology(dataset, scope):
    store = EvidenceStore(dataset, scope)
    assert len(store.join(["agent:1"], ["agent:2"], on="session_id")) == 1
    selected = store.temporal(
        ["agent:2", "agent:1"], anchor="2026-10-01T10:00:00Z", after_seconds=60
    )
    assert [e["id"] for e in selected] == ["agent:1", "agent:2"]
    assert store.coverage()["unknown_chronology"] == 1
    scope["time_start"] = scope["time_end"] = None
    with pytest.raises(EvidenceError, match="unknown_chronology"):
        EvidenceStore(dataset, scope).temporal(["agent:5"], anchor="2026-10-01T10:00:00Z")


def test_identity_provenance_ambiguity_and_forbidden_alias(dataset, scope):
    dataset["manifest"]["identities"] = [
        {"subject": "alice", "aliases": ["A"], "evidence_ids": ["agent:1"]}
    ]
    assert EvidenceStore(dataset, scope).resolve_identity("A") == {
        "subject": "alice",
        "evidence_ids": ["agent:1"],
    }
    dataset["manifest"]["identities"].append(
        {"subject": "bob", "aliases": ["A"], "evidence_ids": ["agent:3"]}
    )
    with pytest.raises(EvidenceError, match="ambiguous"):
        EvidenceStore(dataset, scope).resolve_identity("A")
    dataset["manifest"]["identities"] = [
        {"subject": "alice", "aliases": ["A"], "evidence_ids": ["agent:3"]}
    ]
    with pytest.raises(EvidenceError, match="unauthorized"):
        EvidenceStore(dataset, scope).resolve_identity("A")


def test_coverage_independent_of_record_counts_and_self_assertions(dataset, scope):
    # Use a complete two-record normalized snapshot for the positive control.
    dataset = ingest_agent(
        [
            {"id": "1", "actor": "alice", "occurred_at": "2026-10-01T10:00:00Z"},
            {"id": "2", "actor": "alice", "occurred_at": "2026-10-01T10:01:00Z"},
        ],
        dataset_id="d",
        universe={"accounts": ["alice"]},
    )
    dataset["manifest"]["coverage"].update(
        status="complete", scope=copy.deepcopy(scope), inventory_digest="asserted"
    )
    dataset["manifest"]["sources"][0]["verification"]["coverage"] = "verified"
    store = EvidenceStore(dataset, scope)
    assert not store.coverage()["native_verified"]
    assert not store.coverage()["negative_claim_permitted"]

    class IndependentVerifier:
        def verify(self, records, manifest):
            assert len(records) == 2  # All original records, before authorization filtering.
            return {"authenticity": "verified", "coverage": "verified"}

    checked = EvidenceStore(dataset, scope, native_verifier=IndependentVerifier())
    assert checked.coverage()["status"] == "complete"
    assert not checked.coverage()["negative_claim_permitted"]
    checked.search("")
    assert checked.coverage()["negative_claim_permitted"]
    scope["subjects"] = ["bob"]
    assert not EvidenceStore(dataset, scope).coverage()["negative_claim_permitted"]
    scope["required_sources"] = ["slack"]
    coverage = EvidenceStore(dataset, scope).coverage()
    assert coverage["missing_sources"] == ["slack"]
    assert "empty_authorized_population" in coverage["limitations"]


def test_loss_is_explicit_coverage_failure(dataset, scope):
    dataset["events"][0]["provenance"]["loss"] = ["upstream truncated response"]
    dataset["events"][0]["attributes"]["truncated"] = True
    coverage = EvidenceStore(dataset, scope).coverage()
    assert "lossy_or_truncated_evidence" in coverage["limitations"]


def test_large_indexed_corpus_pagination_and_sql():
    dataset = ingest_agent(
        (
            {
                "id": str(i),
                "actor": "alice",
                "session_id": f"s{i % 100}",
                "content": f"observation {i}",
            }
            for i in range(12_000)
        ),
        dataset_id="large",
        universe={"independent_expected_events": 12_000},
    )
    scope = {
        "dataset_ids": ["large"],
        "subjects": ["alice"],
        "required_sources": ["agent"],
        "fields": ["id", "actor", "session_id", "content"],
        "time_start": None,
        "time_end": None,
    }
    store = EvidenceStore(dataset, scope)
    assert store.query_sql("SELECT count(*) AS n FROM events WHERE session_id='s17'") == [
        {"n": 120}
    ]
    assert len(store.search("", limit=100, offset=11_900)) == 100
    assert store.coverage()["authorized_events"] == 12_000
    assert not store.coverage()["negative_claim_permitted"]


def test_missing_normalized_rows_rejected_even_with_original_bytes(dataset, scope):
    dataset["events"] = dataset["events"][:2]
    with pytest.raises(ValueError, match="normalization_coverage_gap"):
        EvidenceStore(dataset, scope)


def test_temporal_join_and_artifact_provenance_are_scoped(dataset, scope):
    dataset["events"][0]["artifact_ids"] = ["a@1"]
    dataset["events"][2]["artifact_ids"] = ["a@1"]
    store = EvidenceStore(dataset, scope)
    joined = store.temporal_join(["agent:1"], ["agent:2"], on="session_id", after_seconds=60)
    assert joined[0]["delta_seconds"] == 60
    assert store.temporal_join(["agent:1"], ["agent:2"], on="session_id", after_seconds=59) == []
    assert [e["id"] for e in store.artifact_events("a@1")] == ["agent:1"]
    with pytest.raises(EvidenceError):
        store.temporal_join(["agent:1"], ["agent:3"], on="session_id", after_seconds=60)
    with pytest.raises(EvidenceError, match="artifact_missing_or_unauthorized"):
        store.artifact_events("absent")


def test_long_records_remain_lossless_with_bounded_span_fetch():
    dataset = ingest_agent(
        [{"id": "long", "actor": "alice", "content": "🌍" * 100_000}],
        dataset_id="d",
        universe={"accounts": ["alice"]},
    )
    scope = {
        "dataset_ids": ["d"],
        "subjects": ["alice"],
        "required_sources": ["agent"],
        "fields": EVENT_FIELDS.copy(),
        "time_start": None,
        "time_end": None,
    }
    store = EvidenceStore(dataset, scope, max_result_bytes=1024, allow_native=True)
    with pytest.raises(EvidenceError, match="result_limit"):
        store.fetch(["agent:long"])
    reference = store.reference("agent:long", start=90_000, end=90_010)
    assert store.fetch_reference(reference) == "🌍" * 10
    assert len(store.fetch_native("agent:long", start=0, end=128)) == 128
    assert not store.coverage()["population_fully_examined"]
    with pytest.raises(EvidenceError):
        store.query_sql("SELECT content FROM events")


def test_unknown_actor_never_supports_an_absence_claim(dataset, scope):
    store = EvidenceStore(dataset, scope)
    assert "identity_coverage_unverified" in store.coverage()["limitations"]
    assert store.search("unknown actor") == []
    with pytest.raises(EvidenceError, match="unauthorized"):
        store.fetch(["agent:6"])


@pytest.mark.parametrize("limit", [0, -1, float("nan"), float("inf"), True, "bad"])
def test_invalid_time_budget_is_explicit(dataset, scope, limit):
    with pytest.raises(EvidenceError, match="invalid_resource_limits"):
        EvidenceStore(dataset, scope, max_seconds=limit)


def test_authentic_raw_bytes_cannot_authenticate_modified_normalized_actor(dataset, scope):
    class Verified:
        def verify(self, records, manifest):
            return {"authenticity": "verified", "coverage": "verified"}

    dataset["events"][2]["actor"] = "alice"
    with pytest.raises(ValueError, match="normalized_native_mismatch"):
        EvidenceStore(dataset, scope, native_verifier=Verified())


def test_native_verifier_cannot_verify_subset_and_expose_other_records(dataset, scope):
    class Verified:
        def verify(self, records, manifest):
            return {"authenticity": "verified", "coverage": "verified"}

    dataset["manifest"]["sources"][0]["record_hashes"].pop()
    with pytest.raises(ValueError, match="source_normalization_coverage_gap"):
        EvidenceStore(dataset, scope, native_verifier=Verified())
