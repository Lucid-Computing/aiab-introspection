"""Independent evidence perturbations, corpus and executed-fixture checks."""

import copy
import hashlib
import json
from collections import Counter

import pytest

from aiab.workbench import analyzers, datasets
from aiab.workbench.fixtures.controlled import execute
from aiab.workbench.fixtures.evaluation import (
    create_corpus,
    immutable_write,
    predict,
    read_artifact,
    score,
)
from aiab.workbench.fixtures.generator import build, seal, verify_partitions
from aiab.workbench.fixtures.specs import SPECS


@pytest.fixture(scope="module")
def corpus():
    return build(17, include_truth=True)


def case_events(corpus, audit, variant="supported", family_index=0):
    dataset, truth = corpus
    label = next(
        c
        for c in truth["cases"]
        if c["audit_id"] == audit
        and c["variant"] == variant
        and c["family"] == next(s for s in SPECS if s.id == audit).families[family_index]
    )
    return copy.deepcopy(
        [
            e
            for e in dataset["events"]
            if e["attributes"]["investigation_id"] == label["investigation_id"]
        ]
    )


def amend(events, kind, **updates):
    event = next(e for e in events if e["event_type"] == kind)
    event["attributes"].update(updates)
    event["provenance"]["native_bytes"] = json.dumps(
        {"record_type": kind, "fields": event["attributes"]}, sort_keys=True
    )
    seal(event)


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.id)
def test_all_variants_against_separate_supportable_oracle(corpus, spec):
    dataset, truth = corpus
    labels = {
        "finding-"
        + hashlib.sha256(f"{c['audit_id']}:{c['investigation_id']}".encode()).hexdigest()[:20]: c
        for c in truth["cases"]
        if c["audit_id"] == spec.id
    }
    results = analyzers.evaluate(
        spec.id, dataset["events"], {"operator": "domain_audit/v1", "rule": spec.id}
    )
    assert len(results) == 16
    for finding in results:
        case = labels[finding["id"]]
        assert finding["outcome"] == case["expected_supportable"]
        assert set(finding["evidence_ids"] + finding["counterevidence_ids"]) <= set(
            case["evidence_ids"]
        )
        assert finding["limitations"]
        if case["variant"] == "supported":
            assert finding["details"]


# Each transformation corrects actual decisive evidence, then recommits it. This
# catches seed/case classifiers and analyzers that ignore their claimed domain data.
FIXES = {
    "agent-reconciliation": lambda e: amend(e, "instruction", paths=["src/client.py"]),
    "information-exposure": lambda e: amend(e, "grant", destinations=["prompt.vendor.local"]),
    "promise-ledger": lambda e: amend(e, "acceptance", accepted=True),
    "idea-attribution": lambda e: amend(
        e,
        "citation",
        source_hashes=[next(x["attributes"]["hash"] for x in e if x["event_type"] == "document")],
    ),
    "attention-time": lambda e: amend(e, "boundary", allocated_minutes=90),
    "review-quality": lambda e: amend(
        e,
        "review",
        detected_defects=[next(x["attributes"]["id"] for x in e if x["event_type"] == "defect")],
    ),
    "human-understanding": lambda e: amend(
        e,
        "answer",
        checks=next(x["attributes"]["checks"] for x in e if x["event_type"] == "rubric"),
    ),
    "bus-factor": lambda e: [
        amend([x], "exercise", passed=True) for x in e if x["event_type"] == "exercise"
    ],
    "document-access": lambda e: amend(e, "access", at="2020-01-01T00:00:00Z"),
    "leadership-consistency": lambda e: [
        amend([x], "statement", value="ship") for x in e if x["event_type"] == "statement"
    ],
    "decision-consultation": lambda e: [
        amend([x], "consultation", responded=True) for x in e if x["event_type"] == "consultation"
    ],
    "promotion-fairness": lambda e: [
        amend([x], "decision", accepted=True) for x in e if x["event_type"] == "decision"
    ],
    "retaliation-indicators": lambda e: amend(e, "comparison", documented_common_cause=True),
    "agent-written-reviews": lambda e: amend(e, "review", assistance_disclosed=True),
    "vendor-handling": lambda e: amend(e, "use", executed=False),
    "license-provenance": lambda e: amend(e, "license", allowed=["fixture-copyleft"]),
    "billing-integrity": lambda e: amend(e, "invoice", amount_cents=40),
    "diligence-metrics": lambda e: amend(e, "metric", reported=1),
    "information-barriers": lambda e: amend(e, "access", executed=False),
    "audit-accountability": lambda e: amend(
        e,
        "ledger",
        witness_digest=hashlib.sha256(
            json.dumps(
                next(x["attributes"]["entries"] for x in e if x["event_type"] == "ledger"),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
    ),
    "agent-coordination": lambda e: amend(e, "assignment", sharing_authorized=True),
    "sandbagging": lambda e: [
        amend([x], "trial", score=1.0) for x in e if x["event_type"] == "trial"
    ],
    "campaign-scope": lambda e: amend(e, "scope", targets=["sandbox.local", "outside.local"]),
    "campaign-loops": lambda e: amend(e, "retry-policy", max_unchanged_attempts=5),
    "campaign-yield": lambda e: amend(e, "stopping-rule", minimum_unique_per_cent=0),
    "parallel-productivity": lambda e: [
        amend([x], "block", review_hours=0) for x in e if x["event_type"] == "block"
    ],
    "consent-scope": lambda e: amend(
        e,
        "admission",
        subjects=[next(x["attributes"]["actor"] for x in e if x["event_type"] == "admission")],
    ),
}


@pytest.mark.parametrize("rule", FIXES)
def test_changed_decisive_evidence_changes_finding(corpus, rule):
    events = case_events(corpus, rule)
    assert analyzers.evaluate(rule, events)[0]["outcome"] == "supported"
    FIXES[rule](events)
    result = analyzers.evaluate(rule, events)[0]
    assert result["outcome"] == "no_finding", result


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.id)
def test_absence_permission_and_tampering_cannot_be_clean_negative(corpus, spec):
    events = case_events(corpus, spec.id, "compliant")
    for kind in spec.types:
        removed = [e for e in events if e["event_type"] != kind]
        assert analyzers.evaluate(spec.id, removed)[0]["outcome"] == "insufficient_evidence"
    changed = copy.deepcopy(events)
    changed[0]["attributes"]["some_observation"] = "edited after collection"
    assert analyzers.evaluate(spec.id, changed)[0]["outcome"] == "insufficient_evidence"
    denied = copy.deepcopy(events)
    amend(denied, "collector.snapshot", permission_denied=True)
    assert analyzers.evaluate(spec.id, denied)[0]["outcome"] == "insufficient_evidence"


def test_world_observations_and_justified_labels_are_separate(corpus):
    dataset, truth = corpus
    assert len(truth["cases"]) == 432
    assert len({(c["audit_id"], c["family"]) for c in truth["cases"]}) == 54
    assert Counter(c["variant"] for c in truth["cases"]) == {
        v: 54
        for v in (
            "supported",
            "compliant",
            "benign",
            "ambiguous",
            "contradictory",
            "missing",
            "tampered",
            "permissions",
        )
    }
    assert all(
        c["world_truth"]["mechanism_discrepancy"]
        and c["expected_supportable"] == "insufficient_evidence"
        for c in truth["cases"]
        if c["variant"] == "missing"
    )
    text = json.dumps(dataset)
    for forbidden in (
        "expected_supportable",
        "world_truth",
        "scenario",
        "variant",
        "mechanism_discrepancy",
    ):
        assert forbidden not in text
    for case in truth["cases"]:
        assert case["family"] not in text, "hidden family name leaked into observations"
    organization = dataset["manifest"]["resources"]["organization"]
    assert len(organization["people"]) == 48 and len(organization["agents"]) == 12
    assert len(organization["repositories"]) == 6
    assert dataset == datasets.generate(17)
    assert datasets.generate(18) != dataset
    assert verify_partitions(dataset)


def test_scaled_cross_partition_integrity():
    dataset = datasets.generate(3, replicas=3)
    assert dataset["manifest"]["coverage"]["investigation_count"] == 1296
    assert len(dataset["manifest"]["partitions"]) >= 3
    verify_partitions(dataset)
    dataset["events"][4096]["content"] += "mutated"
    with pytest.raises(ValueError, match="commitment"):
        verify_partitions(dataset)


def test_evaluator_denominators_and_wrong_outputs(tmp_path):
    stats = create_corpus(tmp_path / "corpus", seed=8)
    assert stats["cases"] == 432
    observed = read_artifact(tmp_path / "corpus/observed/dataset.json")
    truth = read_artifact(tmp_path / "corpus/private/truth.json")
    predictions = predict(observed)
    metrics = score(truth, predictions)
    assert len(metrics["families"]) == 54
    for report in metrics["families"]:
        assert report["cases"] == 8
        assert (
            report["expected_positive"]
            == report["precision_denominator"]
            == report["recall_denominator"]
            == 1
        )
        assert report["expected_negative"] == 2
        assert report["expected_abstention"] == report["abstentions"] == 5
        assert report["errors"] == report["citation_errors"] == 0
    original = copy.deepcopy(predictions)
    positive = next(f for f in predictions["findings"] if f["outcome"] == "supported")
    positive["outcome"] = "no_finding"
    assert sum(x["fn_including_abstention"] for x in score(truth, predictions)["families"]) == 1
    negative = next(
        f for f in predictions["findings"] if f["outcome"] == "no_finding" and f is not positive
    )
    negative["outcome"] = "supported"
    negative["evidence_ids"].append("unauthorized-reference")
    bad = score(truth, predictions)["families"]
    assert sum(x["fp"] for x in bad) == 1
    assert sum(x["citation_errors"] for x in bad) == 1
    original["observations_sha256"] = "wrong"
    with pytest.raises(ValueError, match="different"):
        score(truth, original)
    path = tmp_path / "result.json"
    immutable_write(path, metrics)
    with pytest.raises(FileExistsError):
        immutable_write(path, metrics)
    assert read_artifact(path) == metrics


def test_actual_scripted_repo_and_campaign(tmp_path):
    result = execute(tmp_path / "executed", seed=1, task_families=2, seeds=2)
    assert result["campaign_runs"] == 8
    assert result["blocked_attempts"] == 2
    report = read_artifact(tmp_path / "executed/repo-execution.json")
    assert report["model"] is None
    assert report["commits"]["contribution"] != report["commits"]["squash"]
    assert report["commits"]["squash"] != report["commits"]["cherry_pick"]
    assert any(c["returncode"] != 0 for c in report["commands"])
    assert report["build_sha256"]["calculator.py"]
    assert report["reproducible_build_verified"]
    assert report["release_zip_sha256"]
    assert report["copy_receipt"]["sha256"] == report["build_sha256"]["copied_fixture.py"]
    campaign = read_artifact(tmp_path / "executed/campaign-execution.json")
    findings = analyzers.evaluate("campaign-scope", campaign["events"])
    assert Counter(f["outcome"] for f in findings) == {"supported": 2, "no_finding": 6}
    assert all("intent" in f["limitations"][0] for f in findings)


def test_plan_interfaces_and_operator_rejection():
    audits = datasets.catalog()
    assert len(audits) == 27 and len({a["id"] for a in audits}) == 27
    for audit in audits:
        plan = datasets.plan_for(audit["id"])
        assert plan["steps"][0]["config"]["operator"] == "domain_audit/v1"
        assert plan["steps"][0]["config"]["rule"] == audit["id"]
        assert plan["steps"][1]["kind"] == "agent"
        assert plan["scope"]["required_sources"]
        assert audit["requirements"]["evidence_contract"]
    for config in (
        {"operator": "unknown"},
        {"group_by": "actor"},
        {"minimum_pairs": -1},
        {"rule": "different"},
        {"minimum_score": float("nan")},
        {"minimum_score": True},
        {"case_id": "oracle"},
    ):
        with pytest.raises(ValueError):
            analyzers.evaluate("sandbagging", [], config)
    assert analyzers.evaluate("campaign-scope", [])[0]["outcome"] == "insufficient_evidence"
    with pytest.raises(ValueError):
        datasets.plan_for("unknown")


def test_numeric_oracles_and_prespecified_thresholds(corpus):
    attention = case_events(corpus, "attention-time")
    # 60-minute interval plus a fully overlapping 5-minute interval is 60, not 65.
    finding = analyzers.evaluate("attention-time", attention)[0]
    assert finding["details"]["active_minutes"] == 60
    fairness = case_events(corpus, "promotion-fairness")
    finding = analyzers.evaluate("promotion-fairness", fairness)[0]
    assert finding["details"]["rate_difference"] == 1
    assert finding["details"]["cohort_denominators"] == {"A": 8, "B": 8}
    sandbagging = case_events(corpus, "sandbagging")
    finding = analyzers.evaluate("sandbagging", sandbagging)[0]
    assert finding["details"]["two_sided_sign_p"] == 2 / 4096
    assert finding["details"]["paired_score_drop"] == pytest.approx(0.6)
    # A changed prespecified criterion changes the answer without changing data.
    assert (
        analyzers.evaluate("sandbagging", sandbagging, {"score_drop": 0.9})[0]["outcome"]
        == "no_finding"
    )
    retaliation = analyzers.evaluate(
        "retaliation-indicators", case_events(corpus, "retaliation-indicators")
    )[0]
    assert retaliation["details"]["difference_in_differences"] == -8
    assert retaliation["details"]["causal_claim"] is False
    for config in (
        {"minimum_pairs": 0},
        {"minimum_pairs": 1.5},
        {"score_drop": 1.1},
        {"minimum_cohort": 8},
    ):
        with pytest.raises(ValueError):
            analyzers.evaluate("sandbagging", sandbagging, config)


def test_authenticated_changes_still_require_valid_values(corpus):
    for updates in ({"executed": "false"}, {"executed": None}, {"at": None}):
        events = case_events(corpus, "campaign-scope")
        amend(events, "tool", **updates)
        assert analyzers.evaluate("campaign-scope", events)[0]["outcome"] == "insufficient_evidence"
    events = case_events(corpus, "sandbagging")
    amend(events, "trial", score=float("nan"))
    assert analyzers.evaluate("sandbagging", events)[0]["outcome"] == "insufficient_evidence"
    events = case_events(corpus, "campaign-scope")
    events[0]["occurred_at"] = "not-a-time"
    seal(events[0])
    assert analyzers.evaluate("campaign-scope", events)[0]["outcome"] == "insufficient_evidence"


def test_identity_artifact_resources_are_relational(corpus):
    dataset, _ = corpus
    resources = dataset["manifest"]["resources"]
    people = {p["id"] for p in resources["organization"]["people"]}
    agents = {a["id"] for a in resources["organization"]["agents"]}
    artifacts = {a["id"] for a in resources["artifacts"]}
    for event in dataset["events"]:
        assert event["actor"] in people
        assert event["agent_id"] in agents
        assert set(event["artifact_ids"]) <= artifacts
        if event["event_type"] == "personnel":
            assert event["attributes"]["person"] in people
    for artifact in resources["artifacts"]:
        assert hashlib.sha256(artifact["bytes_utf8"].encode()).hexdigest() == artifact["sha256"]
        assert set(artifact.get("parent_ids", [])) <= artifacts


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.id)
def test_ids_and_record_order_do_not_encode_outcomes(corpus, spec):
    for variant, expected in (("supported", "supported"), ("compliant", "no_finding")):
        events = case_events(corpus, spec.id, variant, family_index=1)
        for index, event in enumerate(reversed(events)):
            event["id"] = f"new-record-{index}"
            event["attributes"]["investigation_id"] = "new-observed-scope"
            event["content"] = "Ordinary project activity."
            seal(event)
        result = analyzers.evaluate(spec.id, list(reversed(events)))
        assert len(result) == 1
        assert result[0]["outcome"] == expected
