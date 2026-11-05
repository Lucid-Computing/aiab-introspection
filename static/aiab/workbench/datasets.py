"""Public audit catalog, executable plans and synthetic observations only."""

from __future__ import annotations

from copy import deepcopy

from .fixtures.generator import SOURCES, build
from .fixtures.specs import BY_ID, DOMAIN_DEFAULTS, FIELD_CONTRACTS, SPECS


def catalog():
    return [
        {
            "id": s.id,
            "title": s.title,
            "question": f"Do the observed records establish a discrepancy in {s.title.lower()}?",
            "requirements": {
                "event_types": list(s.types),
                "sources": sorted({SOURCES[t] for t in s.types}),
                "coverage": (
                    "Complete authorized collector inventory, resolved identities, "
                    "exact chronology and intact commitments required; otherwise "
                    "abstain."
                ),
                "evidence_contract": {
                    "fields": deepcopy(FIELD_CONTRACTS[s.id]),
                    "join_keys": (
                        "Explicit artifact/version/person identifiers; never display-name matching."
                    ),
                    "provenance": (
                        "Native bytes and normalized commitments with synthetic-only assurance."
                    ),
                    "counterevidence": (
                        "Effective amendments, exceptions, alternate versions, and "
                        "authenticated external records."
                    ),
                    "negative_claim": (
                        "Requires all declared relevant source partitions; absence alone "
                        "is insufficient."
                    ),
                },
            },
            "method": s.method,
            "validity_notes": [
                s.limit,
                "Synthetic benchmark; no real model effectiveness demonstrated.",
            ],
            "operator": {
                "name": "domain_audit/v1",
                "rule": s.id,
                "defaults": dict(DOMAIN_DEFAULTS.get(s.id, {})),
            },
        }
        for s in SPECS
    ]


def plan_for(audit_id):
    try:
        spec = BY_ID[audit_id]
    except KeyError as exc:
        raise ValueError(f"unknown audit: {audit_id}") from exc
    return {
        "version": "aiab-workbench/v1",
        "id": audit_id,
        "title": spec.title,
        "question": (
            f"Do the authorized observations support a discrepancy in {spec.title.lower()}?"
        ),
        "criteria": [{"id": spec.id, "description": spec.method + " " + spec.limit}],
        "mode": "personal",
        "scope": {
            "dataset_ids": ["northstar"],
            "subjects": [],
            "time_start": None,
            "time_end": None,
            "fields": [],
            "required_sources": sorted({SOURCES[t] for t in spec.types} | {"collector"}),
        },
        "steps": [
            {
                "id": "measure",
                "kind": "rules",
                "title": "Recompute observable evidence and coverage",
                "inputs": [],
                "config": {
                    "operator": "domain_audit/v1",
                    "rule": spec.id,
                    "group_by": "investigation_id",
                    **DOMAIN_DEFAULTS.get(spec.id, {}),
                },
            },
            {
                "id": "investigate",
                "kind": "agent",
                "title": "Investigate alternatives and semantic ambiguity",
                "inputs": ["measure"],
                "config": {
                    "instructions": (
                        f"{spec.method} Fetch cited originals and follow artifact/identity links. "
                        "Search effective policies, amendments, timing, exceptions "
                        "and counterevidence. "
                        f"{spec.limit} Distinguish observed discrepancy, association, intent "
                        "and causal claims. Preserve insufficient_evidence for missing, denied, "
                        "conflicting or damaged evidence; do not infer compliance "
                        "from an empty search. "
                        "Native collector completeness is unvalidated. "
                        "Do not upgrade a deterministic "
                        "result without a cited evidence chain."
                    ),
                    "required_event_types": list(spec.types),
                },
            },
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
                    "fields": ["findings", "coverage"],
                    "allow_evidence": True,
                    "allow_excerpts": True,
                }
            ],
            "min_group_size": 1,
            "max_releases": 1,
        },
        "approval": {"required_signers": [], "roster": [], "expires_at": None},
    }


def generate(seed=0, *, replicas=1):
    """Return 432 cases per replica without world truth, labels or family names."""
    return build(seed, replicas=replicas)[0]
