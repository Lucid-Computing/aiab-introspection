"""Independent execution audit: synthetic observations; no real inference or service changes."""

import asyncio
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from aiab.workbench import datasets
from aiab.workbench.analyzers import evaluate
from aiab.workbench.evidence import EvidenceStore
from aiab.workbench.fixtures.evaluation import predict, score
from aiab.workbench.fixtures.generator import build, seal
from aiab.workbench.fixtures.specs import FIELD_CONTRACTS, SPECS
from aiab.workbench.runner import execute_plan
from aiab.workbench.tools import RuntimeFailure, ToolBroker


@pytest.fixture(scope="module")
def corpus():
    return build(93, include_truth=True)


def case_events(corpus, rule, expected="supported"):
    dataset, truth = corpus
    case = next(
        c for c in truth["cases"] if c["audit_id"] == rule and c["expected_supportable"] == expected
    )
    return deepcopy([e for e in dataset["events"] if e["id"] in case["evidence_ids"]])


def reseal_inventory(events):
    snapshot = next(e for e in events if e["event_type"] == "collector.snapshot")
    snapshot["attributes"]["counts"] = dict(
        Counter(e["event_type"] for e in events if e["event_type"] != "collector.snapshot")
    )
    seal(snapshot)


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.id)
def test_new_seed_missing_and_conflicting_source_all_methods(corpus, spec):
    events = case_events(corpus, spec.id)
    assert evaluate(spec.id, events)[0]["outcome"] == "supported"
    removed = next(e for e in events if e["event_type"] != "collector.snapshot")
    assert (
        evaluate(spec.id, [e for e in events if e is not removed])[0]["outcome"]
        == "insufficient_evidence"
    )
    malformed = deepcopy(events)
    snapshot = next(e for e in malformed if e["event_type"] == "collector.snapshot")
    snapshot["attributes"]["complete"] = "true"
    seal(snapshot)
    assert evaluate(spec.id, malformed)[0]["outcome"] == "insufficient_evidence"
    missing_field = deepcopy(events)
    record = next(e for e in missing_field if e["event_type"] in FIELD_CONTRACTS[spec.id])
    field = FIELD_CONTRACTS[spec.id][record["event_type"]][0]
    del record["attributes"][field]
    seal(record)
    assert evaluate(spec.id, missing_field)[0]["outcome"] == "insufficient_evidence"
    conflict = deepcopy(removed)
    conflict["id"] += "-conflicting-version"
    conflict["provenance"]["native_bytes"] += " conflicting native version"
    seal(conflict)
    events.append(conflict)
    reseal_inventory(events)
    finding = evaluate(spec.id, events)[0]
    assert finding["outcome"] == "insufficient_evidence"
    assert set(finding["counterevidence_ids"]) == {removed["id"], conflict["id"]}


def test_score_missing_predictions_keeps_all_denominators(corpus):
    dataset, truth = corpus
    predictions = predict(dataset)
    truth = {**truth, "observations_sha256": predictions["observations_sha256"]}
    full = score(truth, predictions)
    assert full["cases"] == 432
    assert sum(f["exact_supportable_matches"] for f in full["families"]) == 432
    empty = score(truth, {**predictions, "findings": []})
    assert sum(f["missing_predictions"] for f in empty["families"]) == 432
    assert sum(f["recall_denominator"] for f in empty["families"]) == sum(
        c["expected_supportable"] == "supported" for c in truth["cases"]
    )
    assert all(
        f["precision"] is None and f["precision_denominator"] == 0 for f in empty["families"]
    )


@pytest.mark.xfail(
    strict=True, reason="EXE-01: contradictory relational keys overwrite earlier observations"
)
def test_contradictory_assignment_is_order_independent_abstention(corpus):
    events = case_events(corpus, "retaliation-indicators")
    original = next(
        e
        for e in events
        if e["event_type"] == "assignment"
        and e["attributes"]["cohort"] == "raised"
        and e["attributes"]["period"] == "after"
    )
    conflict = deepcopy(original)
    conflict["id"] += "-contradiction"
    conflict["native_id"] += "-contradiction"
    conflict["attributes"]["access_units"] = 10
    seal(conflict)
    events.append(conflict)
    reseal_inventory(events)
    outcomes = [
        evaluate("retaliation-indicators", order)[0]["outcome"]
        for order in (events, list(reversed(events)))
    ]
    assert outcomes == ["insufficient_evidence", "insufficient_evidence"], outcomes


@pytest.mark.xfail(
    strict=True, reason="EXE-02: earlier approval survives later pre-admission revocation"
)
def test_consent_revocation_invalidates_prior_approval(corpus):
    events = case_events(corpus, "consent-scope", "no_finding")
    admission = next(e["attributes"] for e in events if e["event_type"] == "admission")
    approval = next(
        e
        for e in events
        if e["event_type"] == "consent" and e["attributes"]["person"] == admission["actor"]
    )
    revoked = deepcopy(approval)
    revoked["id"] += "-revocation"
    revoked["native_id"] += "-revocation"
    revoked_at = (datetime.fromisoformat(admission["at"]) - timedelta(seconds=1)).isoformat()
    revoked["attributes"].update(revoked=True, at=revoked_at)
    seal(revoked)
    events.append(revoked)
    reseal_inventory(events)
    finding = evaluate("consent-scope", events)[0]
    assert finding["outcome"] in {"supported", "insufficient_evidence"}, finding


@pytest.mark.xfail(strict=True, reason="EXE-03: unmatched review version produces no_finding")
def test_unmatched_review_version_abstains(corpus):
    events = case_events(corpus, "review-quality")
    review = next(e for e in events if e["event_type"] == "review")
    review["attributes"]["diff_hash"] = "unrelated-version"
    seal(review)
    finding = evaluate("review-quality", events)[0]
    assert finding["outcome"] == "insufficient_evidence", finding


async def test_join_null_keys_and_exact_temporal_boundary(corpus):
    dataset, _ = corpus
    scope = datasets.plan_for(SPECS[0].id, dataset=dataset)["scope"]
    with_store = EvidenceStore(dataset, scope)
    try:
        broker = ToolBroker(with_store)
        event = broker.events[0]
        args = {
            "left_ids": [event["id"]],
            "right_ids": [event["id"]],
            "on": "id",
            "within_seconds": 0,
        }
        assert broker.join(args)["rows"] == [{"left_id": event["id"], "right_id": event["id"]}]
        with pytest.raises(RuntimeFailure, match="unauthorized_evidence_reference"):
            broker.join({**args, "right_ids": ["tester-nonexistent-event"]})
    finally:
        with_store.close()


async def test_cancel_between_deterministic_steps_discards_result(corpus):
    dataset, _ = corpus
    plan = datasets.plan_for(SPECS[0].id, dataset=dataset)
    plan["steps"] = [
        {"id": "select", "kind": "select", "title": "Select", "inputs": [], "config": {}},
        {
            "id": "assert",
            "kind": "assert",
            "title": "Assert",
            "inputs": ["select"],
            "config": {"min_events": 1},
        },
    ]
    ended = []

    def cancel(event):
        if event["kind"] == "step_end":
            ended.append(event["data"]["id"])
            asyncio.current_task().cancel()

    actor = {
        "id": "tester",
        "authorized_dataset_ids": [dataset["id"]],
        "authorized_subjects": plan["scope"]["subjects"],
    }
    task = asyncio.create_task(execute_plan(plan, dataset, actor=actor, on_event=cancel))
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ended == ["select"]


async def test_join_product_and_response_budgets_fail_without_truncation(corpus):
    dataset, _ = corpus
    scope = datasets.plan_for(SPECS[0].id, dataset=dataset)["scope"]
    scope["fields"] = ["id", "actor", "occurred_at"]
    store = EvidenceStore(dataset, scope)
    try:
        broker = ToolBroker(store)
        ident = broker.events[0]["id"]
        with pytest.raises(RuntimeFailure, match="join_budget"):
            await broker.call(
                "join", {"left_ids": [ident] * 317, "right_ids": [ident] * 317, "on": "id"}
            )
        with pytest.raises(RuntimeFailure, match="tool_output_limit"):
            await broker.call(
                "join", {"left_ids": [ident] * 100, "right_ids": [ident] * 100, "on": "id"}
            )
        assert (
            broker.join({"left_ids": [ident], "right_ids": [ident], "on": "campaign_id"})["rows"]
            == []
        )
    finally:
        store.close()
