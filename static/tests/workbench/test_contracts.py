import copy
import math

import pytest

from aiab.workbench.contracts import (
    VERSION,
    canonical_json,
    dataset_digest,
    manifest_digest,
    plan_digest,
    source_digest,
    validate_dataset,
    validate_finding,
    validate_plan,
)
from aiab.workbench.ingest import ingest_agent


@pytest.fixture
def plan():
    return {
        "version": VERSION,
        "id": "p",
        "title": "Campaign",
        "question": "Scope?",
        "criteria": [{"id": "scope", "description": "Check executed actions"}],
        "mode": "personal",
        "scope": {
            "dataset_ids": ["d"],
            "subjects": [],
            "fields": [],
            "required_sources": [],
            "time_start": None,
            "time_end": None,
        },
        "steps": [
            {"id": "first", "kind": "agent", "title": "Investigate", "inputs": [], "config": {}}
        ],
        "agent": {
            "provider": "tinfoil",
            "model": "deepseek-v4-1-flash",
            "max_turns": 12,
            "max_tokens": 32000,
            "max_tool_calls": 40,
            "max_seconds": 180,
        },
        "disclosure": {
            "recipients": [
                {
                    "id": "owner",
                    "role": "owner",
                    "fields": ["findings"],
                    "allow_evidence": True,
                    "allow_excerpts": True,
                }
            ],
            "min_group_size": 1,
            "max_releases": 1,
        },
        "approval": {"required_signers": [], "roster": [], "expires_at": None},
    }


def test_canonical_plan_and_domain_separation(plan):
    assert validate_plan(plan) is plan
    reordered = dict(reversed(list(plan.items())))
    assert plan_digest(plan) == plan_digest(reordered)
    assert len(plan_digest(plan)) == 64
    assert len({source_digest(plan), manifest_digest(plan), plan_digest(plan)}) == 3
    other = copy.deepcopy(plan)
    other["steps"][0]["config"]["resource_commitment"] = "changed"
    assert plan_digest(plan) != plan_digest(other)


@pytest.mark.parametrize("value", [math.nan, math.inf, 2**60, {1: "bad"}, b"bad"])
def test_noncanonical_rejected(value):
    with pytest.raises(ValueError):
        canonical_json(value)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(version="aiab-graph/v1"),
        lambda p: p["steps"][0].update(inputs=["first"]),
        lambda p: p["steps"][0].update(inputs=["unknown"]),
        lambda p: p["steps"].append(p["steps"][0].copy()),
        lambda p: p["agent"].update(provider="other"),
        lambda p: p["agent"].update(max_turns=True),
        lambda p: p["scope"].update(time_start="2026-01-01"),
        lambda p: p["scope"].update(
            time_start="2026-01-02T00:00:00Z", time_end="2026-01-01T00:00:00Z"
        ),
        lambda p: p["scope"].update(fields=["made_up"]),
    ],
)
def test_invalid_plan(plan, mutation):
    mutation(plan)
    with pytest.raises(ValueError):
        validate_plan(plan)


def test_dataset_duplicate_and_truncation_errors():
    dataset = ingest_agent(
        [{"id": "1", "actor": "alice", "content": "hello"}],
        dataset_id="d",
        universe={"accounts": ["alice"]},
    )
    assert validate_dataset(dataset) is dataset
    assert dataset_digest(dataset) != manifest_digest(dataset["manifest"])
    event = copy.deepcopy(dataset["events"][0])
    event["id"] = "other"
    dataset["events"].append(event)
    with pytest.raises(ValueError, match="duplicate_native_conflict"):
        validate_dataset(dataset)
    dataset["events"].pop()
    dataset["events"][0]["attributes"]["truncated"] = True
    with pytest.raises(ValueError, match="truncation"):
        validate_dataset(dataset)


def test_finding_distinguishes_abstention_from_suppression():
    finding = {
        "id": "f",
        "criterion_id": "c",
        "title": "Unknown",
        "claim": "Missing data",
        "outcome": "insufficient_evidence",
        "evidence_ids": [],
        "counterevidence_ids": [],
        "limitations": ["missing source"],
        "method": "rules",
        "details": {},
    }
    assert validate_finding(finding) is finding
    finding["outcome"] = "suppressed"
    with pytest.raises(ValueError):
        validate_finding(finding)
