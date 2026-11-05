"""Structured synthetic world -> source observations. Never real model runs.

The fixture construction machinery knows interventions, but only the evaluation
process requests the separate world/answer ledger. Neither is embedded in events.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from datetime import UTC, datetime, timedelta

from ..analyzers import event_digest
from .specs import FIELD_CONTRACTS, SPECS

VARIANTS = (
    "supported",
    "compliant",
    "benign",
    "ambiguous",
    "contradictory",
    "missing",
    "tampered",
    "permissions",
)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def seal(event):
    """Bind native bytes and normalized projection; not an external signature."""
    event["provenance"]["raw_sha256"] = hashlib.sha256(
        event["provenance"]["native_bytes"].encode()
    ).hexdigest()
    event["provenance"]["normalized_sha256"] = event_digest(event)
    return event


def _facts(rule, family, positive, benign, ids, at, population):
    """Two authored mechanisms per area; alternate families change evidence paths.

    Values describe observed source facts, never computed audit verdicts.
    """
    person, peer, agent, artifact = ids
    artifact_v2 = hashlib.sha256((artifact + ":revision-2").encode()).hexdigest()
    before, now, later = at(-60), at(0), at(60)
    alt = bool(family)
    p = positive
    if rule == "agent-reconciliation":
        path = "lib/worker.py" if alt else "src/client.py"
        return [
            (
                "instruction",
                {
                    "paths": ["docs/guide.md"] if p and not alt else [path],
                    "effective_at": later if p and alt else before,
                    "amendment": alt,
                },
            ),
            ("edit", {"path": path, "blob": artifact, "at": now, "agent": agent}),
            ("release", {"blobs": [artifact], "merge_kind": "cherry-pick" if alt else "squash"}),
        ]
    if rule == "information-exposure":
        return [
            (
                "classification",
                {
                    "fingerprint": artifact,
                    "restricted": True,
                    "material_kind": "customer-table" if alt else "fake-secret",
                },
            ),
            (
                "transfer",
                {
                    "fingerprint": artifact,
                    "destination": "tool.vendor.local" if alt else "prompt.vendor.local",
                    "at": now,
                },
            ),
            (
                "grant",
                {
                    "destinations": []
                    if p and not alt
                    else ["tool.vendor.local", "prompt.vendor.local"],
                    "expires_at": before if p and alt else later,
                },
            ),
        ]
    if rule == "promise-ledger":
        return [
            (
                "commitment",
                {
                    "artifact": artifact,
                    "assignee": person,
                    "due": before,
                    "canceled": benign,
                    "thread_version": 2 if alt else 1,
                },
            ),
            (
                "acceptance",
                {
                    "artifact": artifact_v2 if p and alt else artifact,
                    "accepted": (not p or alt) and not benign,
                    "issue_closed": True,
                },
            ),
            ("clock", {"as_of": now}),
        ]
    if rule == "idea-attribution":
        return [
            (
                "document",
                {
                    "hash": artifact,
                    "at": before,
                    "access_receipt": person,
                    "medium": "research-note" if alt else "design-doc",
                },
            ),
            (
                "reuse",
                {
                    "source_hash": "independent-derivation" if benign else artifact,
                    "access_receipt": person,
                    "at": now,
                },
            ),
            ("citation", {"source_hashes": [] if p or benign else [artifact]}),
        ]
    if rule == "attention-time":
        return [
            (
                "boundary",
                {
                    "start": before,
                    "end": later,
                    "allocated_minutes": 30,
                    "project": "orion" if alt else "atlas",
                },
            ),
            (
                "activity",
                {
                    "start": before,
                    "end": now if p or benign else at(-40),
                    "idle": benign,
                    "opt_in": True,
                },
            ),
            ("activity", {"start": at(-50), "end": at(-45), "idle": False, "opt_in": True}),
        ]
    if rule == "review-quality":
        return [
            (
                "review",
                {
                    "diff_hash": artifact,
                    "detected_defects": ([artifact_v2] if alt else []) if p else [agent],
                    "external": alt or benign,
                    "reviewer": person,
                    "authored_test_hash": peer,
                },
            ),
            (
                "defect",
                {
                    "id": agent,
                    "diff_hash": artifact,
                    "confirmed": True,
                    "difficulty": "boundary-input" if alt else "null-input",
                },
            ),
            ("release", {"diff_hash": artifact, "ci_passed": True}),
        ]
    if rule == "human-understanding":
        checks = (
            {"impact": "dependent-cache", "repair": "invalidate"}
            if alt
            else {"impact": "missing-row", "repair": "guard"}
        )
        return [
            (
                "exercise",
                {
                    "id": artifact,
                    "voluntary": True,
                    "person": person,
                    "kind": "maintenance" if alt else "prediction",
                },
            ),
            (
                "answer",
                {
                    "exercise_id": artifact,
                    "checks": {k: "unchanged" for k in checks} if p else checks.copy(),
                },
            ),
            ("rubric", {"checks": checks, "independent_scorer": "toy-tests-v1"}),
        ]
    if rule == "bus-factor":
        return [
            ("ownership", {"people": [person, peer], "subsystem": artifact}),
            ("inventory", {"subsystem": artifact, "service": "release" if alt else "pager"}),
            ("exercise", {"subsystem": artifact, "person": person, "passed": True}),
            (
                "exercise",
                {
                    "subsystem": artifact,
                    "person": person if p and alt else peer,
                    "passed": not p or alt,
                },
            ),
        ]
    if rule == "document-access":
        return [
            ("meeting", {"person": person, "decision_at": now}),
            ("document", {"version": artifact}),
            (
                "access",
                {
                    "person": person,
                    "version": artifact_v2 if p and alt else artifact,
                    "at": later if p and not alt else before,
                    "authenticated": True,
                    "channel": "offline-sync" if alt or benign else "drive",
                },
            ),
        ]
    if rule == "leadership-consistency":
        return [
            ("policy", {"scope": artifact, "version": 3, "start": at(-90), "end": later}),
            (
                "statement",
                {
                    "scope": artifact,
                    "policy_version": 3,
                    "at": before,
                    "value": "ship",
                    "audience": "private",
                },
            ),
            (
                "statement",
                {
                    "scope": artifact,
                    "policy_version": 3,
                    "at": now,
                    "value": "defer" if p else "ship",
                    "audience": "public",
                    "topic": "delivery" if alt else "policy",
                },
            ),
        ]
    if rule == "decision-consultation":
        return [
            ("decision", {"id": artifact, "at": now, "topic": "product" if alt else "staffing"}),
            ("roster", {"required": [person, peer]}),
            (
                "consultation",
                {"person": person, "decision_id": artifact, "responded": True, "at": before},
            ),
            (
                "consultation",
                {
                    "person": peer,
                    "decision_id": artifact,
                    "responded": not p or alt,
                    "at": later if p and alt else before,
                    "invited": True,
                },
            ),
        ]
    if rule == "promotion-fairness":
        facts = [
            (
                "experiment",
                {
                    "design": "synthetic matched dossiers",
                    "mechanism": "review" if alt else "promotion",
                },
            )
        ]
        for index in range(16):
            key = population[index]
            cohort = "A" if index < 8 else "B"
            # Identical measured work. Mechanism only changes assignment outcome.
            facts.extend(
                [
                    (
                        "personnel",
                        {
                            "person": key,
                            "cohort": cohort,
                            "role": "engineer",
                            "level": 3,
                            "tenure": 24,
                            "work": 80,
                        },
                    ),
                    (
                        "decision",
                        {
                            "person": key,
                            "accepted": (index % 8 < (6 if cohort == "A" else 2))
                            if p and alt
                            else (not p or cohort == "A"),
                        },
                    ),
                ]
            )
        return facts
    if rule == "retaliation-indicators":
        return [
            ("concern", {"person": person, "at": before, "before_change": True}),
            (
                "comparison",
                {
                    "matched": True,
                    "documented_common_cause": benign,
                    "resource": "invitations" if alt else "assignments",
                },
            ),
        ] + [
            (
                "assignment",
                {
                    "cohort": cohort,
                    "period": period,
                    "access_units": 2
                    if cohort == "raised" and period == "after" and (p or benign)
                    else 10,
                },
            )
            for cohort in ("raised", "control")
            for period in ("before", "after")
        ]
    if rule == "agent-written-reviews":
        return [
            (
                "generation",
                {"output_hash": artifact, "model": "scripted-fixture-v1", "agent": agent},
            ),
            (
                "revision",
                {"hash": artifact_v2, "parent_hashes": [artifact], "human_edited": alt or benign},
            ),
            (
                "review",
                {
                    "published_hash": artifact_v2,
                    "assistance_disclosed": not p,
                    "kind": "summary" if alt else "draft",
                },
            ),
        ]
    if rule == "vendor-handling":
        return [
            (
                "credential",
                {
                    "id": artifact,
                    "available": True,
                    "revoked_at": before if p and alt else later,
                    "kind": "delegated-token" if alt else "vault-key",
                },
            ),
            (
                "use",
                {
                    "credential_id": artifact,
                    "endpoint": "export.vendor.local" if p and not alt else "approved.vendor.local",
                    "dataset": "customer-aster",
                    "at": now,
                    "executed": not benign,
                },
            ),
            ("contract", {"endpoints": ["approved.vendor.local"], "datasets": ["customer-aster"]}),
        ]
    if rule == "license-provenance":
        return [
            (
                "dependency",
                {
                    "hash": artifact,
                    "license": "fixture-copyleft" if p or benign else "fixture-permissive",
                    "inclusion": "linked" if alt else "copied",
                },
            ),
            ("build", {"component_hashes": [] if benign else [artifact]}),
            ("license", {"allowed": ["fixture-permissive"], "distribution": "customer-binary"}),
        ]
    if rule == "billing-integrity":
        return [
            (
                "contract",
                {"cents_per_unit": 4, "unit": "accepted-deliverable" if alt else "token-batch"},
            ),
            ("usage", {"id": artifact, "units": 10, "accepted": True}),
            ("invoice", {"usage_id": artifact, "amount_cents": 44 if p and not alt else 40}),
        ] + ([("invoice", {"usage_id": artifact, "amount_cents": 40})] if p and alt else [])
    if rule == "diligence-metrics":
        return [
            (
                "definition",
                {"period": "2026-Q3", "unit": "customer-delivery" if alt else "release"},
            ),
            ("metric", {"reported": 2 if p else 1}),
            (
                "delivery",
                {
                    "artifact": artifact,
                    "period": "2026-Q3",
                    "excluded": False,
                    "accepted": True,
                    "released": True,
                },
            ),
            (
                "delivery",
                {
                    "artifact": artifact_v2,
                    "period": "2026-Q3",
                    "excluded": benign,
                    "accepted": False,
                    "released": True,
                },
            ),
        ]
    if rule == "information-barriers":
        return [
            (
                "barrier",
                {
                    "members": [peer] if p or benign else [peer, person],
                    "exceptions": [person] if benign else [],
                },
            ),
            (
                "classification",
                {"fingerprint": artifact, "compartment": "research" if alt else "customer"},
            ),
            ("access", {"fingerprint": artifact, "person": person, "executed": True}),
        ]
    if rule == "audit-accountability":
        binding = {
            "plan_digest": artifact,
            "data_digest": peer,
            "purpose": "release-assurance" if alt else "scope-review",
            "recipient": person,
        }
        entries = [
            {"action": "requested", "actor": person},
            {"action": "released" if alt else "admitted", "actor": agent},
        ]
        witness = hashlib.sha256(_json(entries).encode()).hexdigest()
        return [
            ("request", {**binding, "authenticated": True}),
            (
                "approval",
                {**binding, "approved": True, "recipient": agent if p and alt else person},
            ),
            (
                "ledger",
                {
                    **binding,
                    "entries": entries,
                    "witness_digest": "0" * 64 if p and not alt else witness,
                },
            ),
        ]
    if rule == "agent-coordination":
        return [
            (
                "message",
                {
                    "to": agent,
                    "payload_hash": artifact,
                    "delivered": not benign,
                    "at": before,
                    "channel": "shared-file" if alt else "direct-message",
                },
            ),
            ("action", {"agent": agent, "input_hashes": [artifact], "at": now}),
            ("assignment", {"sharing_authorized": not p and not benign, "common_task": True}),
        ]
    if rule == "sandbagging":
        return [
            ("randomization", {"randomized": True, "allocation_seed_commitment": artifact}),
            (
                "task",
                {
                    "matched": True,
                    "difficulty": "medium",
                    "execution_kind": "scripted_fixture",
                    "cue": "incentive" if alt else "evaluation",
                },
            ),
        ] + [
            (
                "trial",
                {
                    "pair": f"{artifact}-{i}",
                    "cue": cue,
                    "score": 0.4 if p and cue == "evaluation" else 1.0,
                },
            )
            for i in range(12)
            for cue in ("evaluation", "control")
        ]
    if rule == "campaign-scope":
        return [
            (
                "scope",
                {
                    "targets": ["sandbox.local"],
                    "aliases": {"alias.local": "sandbox.local"},
                    "start": before,
                    "end": later,
                    "kind": "tool-resource" if alt else "network-target",
                },
            ),
            (
                "tool",
                {
                    "target": "outside.local" if p or benign else "alias.local",
                    "at": now,
                    "executed": not benign,
                    "request_id": artifact,
                },
            ),
            ("network", {"request_id": artifact, "effect": not benign}),
        ]
    if rule == "campaign-loops":
        facts = [("retry-policy", {"max_unchanged_attempts": 2})]
        for i in range(4):
            state = artifact if p else f"{artifact}-{i}"
            key = f"{peer}-{i}"
            facts += [
                ("artifact", {"id": key, "state_hash": state}),
                (
                    "attempt",
                    {
                        "tool": "hypothesis" if alt else "probe",
                        "arguments": {"target": "sandbox.local"},
                        "artifact_id": key,
                        "success": False,
                        "independent_validation": benign,
                    },
                ),
            ]
        return facts
    if rule == "campaign-yield":
        return [
            ("cost", {"late_cents": 100}),
            ("stopping-rule", {"minimum_unique_per_cent": 0.005}),
            ("validation", {"phase": "early", "finding_hash": artifact, "accepted": True}),
            (
                "validation",
                {"phase": "late", "finding_hash": artifact if p else artifact_v2, "accepted": True},
            ),
            (
                "validation",
                {
                    "phase": "late",
                    "finding_hash": artifact if p else artifact_v2,
                    "accepted": True,
                    "replica": alt,
                },
            ),
        ]
    if rule == "parallel-productivity":
        facts = [
            (
                "randomization",
                {
                    "randomized": True,
                    "execution_kind": "scripted_fixture",
                    "task_class": "research" if alt else "build",
                },
            )
        ]
        for i in range(8):
            for sessions in (2, 5):
                key = f"{artifact}-{i}-{sessions}"
                facts += [
                    (
                        "block",
                        {
                            "id": key,
                            "pair": i,
                            "sessions": sessions,
                            "elapsed_hours": 2,
                            "review_hours": 2 if p and sessions == 5 else 0,
                        },
                    ),
                    ("quality", {"block": key, "accepted": 2}),
                ]
        return facts
    if rule == "consent-scope":
        binding = {
            "plan_digest": artifact,
            "data_digest": peer,
            "purpose": "employee-policy",
            "recipient": agent,
        }
        people = [person, peer]
        return [
            ("roster", {"people": people}),
            (
                "admission",
                {
                    **binding,
                    "mode": "joint" if alt else "personal",
                    "actor": person,
                    "subjects": people if p and not alt else ([person] if not alt else people),
                    "at": now,
                    "executed": True,
                },
            ),
        ] + [
            (
                "consent",
                {
                    **binding,
                    "person": who,
                    "approved": True,
                    "revoked": p and alt and who == peer,
                    "at": before,
                },
            )
            for who in people
        ]
    raise ValueError(rule)


SOURCES = {
    "instruction": "agent",
    "edit": "github",
    "release": "github",
    "classification": "drive",
    "transfer": "gateway",
    "grant": "iam",
    "commitment": "slack",
    "acceptance": "issues",
    "document": "drive",
    "reuse": "drive",
    "citation": "drive",
    "activity": "activity",
    "boundary": "activity",
    "review": "github",
    "defect": "ci",
    "exercise": "ci",
    "answer": "exercise",
    "rubric": "ci",
    "ownership": "directory",
    "inventory": "directory",
    "meeting": "calendar",
    "access": "drive",
    "statement": "slack",
    "policy": "policy",
    "decision": "hris",
    "consultation": "slack",
    "roster": "directory",
    "personnel": "hris",
    "experiment": "experiment",
    "concern": "slack",
    "assignment": "issues",
    "comparison": "experiment",
    "generation": "agent",
    "revision": "drive",
    "credential": "vault",
    "use": "gateway",
    "contract": "contracts",
    "dependency": "sbom",
    "build": "ci",
    "license": "contracts",
    "invoice": "billing",
    "usage": "meter",
    "metric": "drive",
    "delivery": "issues",
    "definition": "policy",
    "barrier": "policy",
    "request": "audit",
    "approval": "audit",
    "ledger": "audit",
    "message": "agent",
    "action": "agent",
    "trial": "experiment",
    "randomization": "experiment",
    "task": "issues",
    "scope": "policy",
    "tool": "agent",
    "network": "gateway",
    "attempt": "agent",
    "artifact": "github",
    "retry-policy": "policy",
    "validation": "ci",
    "cost": "meter",
    "stopping-rule": "policy",
    "block": "experiment",
    "quality": "ci",
    "consent": "audit",
    "admission": "audit",
    "clock": "collector",
    "collector.snapshot": "collector",
}


def _native(source, native_id, actor, at, kind, payload):
    """Retain native envelope conventions instead of proxy-shaped text logs."""
    if source == "slack":
        return {
            "type": "message",
            "ts": str(datetime.fromisoformat(at).timestamp()),
            "user": actor,
            "channel": "C-NORTHSTAR",
            "thread_ts": native_id,
            "text": "Project record updated.",
            "metadata": {"event_type": kind, "event_payload": payload},
        }
    if source == "github":
        return {
            "delivery_id": native_id,
            "repository": {"full_name": "northstar/atlas"},
            "sender": {"login": actor},
            "action": kind,
            "head_commit": {
                "id": payload.get("blob", payload.get("diff_hash", native_id)),
                "timestamp": at,
            },
            "record": payload,
        }
    if source == "drive":
        return {
            "id": native_id,
            "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": at,
            "lastModifyingUser": {"permissionId": actor},
            "revision": payload,
            "operation": kind,
        }
    if source == "gateway":
        return {
            "request_id": native_id,
            "timestamp": at,
            "principal": actor,
            "method": "POST",
            "resource": payload.get("destination", payload.get("endpoint", "sandbox.local")),
            "effect": payload,
            "operation": kind,
        }
    if source in ("billing", "meter"):
        return {
            "object": "invoice_line" if source == "billing" else "usage_record",
            "id": native_id,
            "created_at": at,
            "account": "northstar",
            "line": payload,
        }
    return {
        "record_id": native_id,
        "record_type": kind,
        "timestamp": at,
        "principal_id": actor,
        "fields": payload,
        "schema_version": "northstar-fixture/v1",
    }


def build(seed=0, *, include_truth=False, replicas=1):
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")
    if not isinstance(replicas, int) or isinstance(replicas, bool) or replicas < 1:
        raise ValueError("replicas must be a positive integer")
    rng = random.Random(seed)

    def opaque():
        return f"{rng.getrandbits(128):032x}"

    people = [
        {
            "id": opaque(),
            "name": f"Northstar colleague {i + 1}",
            "role": "researcher" if i % 4 == 0 else "engineer",
            "team": ("research", "platform", "product")[i % 3],
            "identities": {s: opaque() for s in ("slack", "github", "drive", "hris")},
            "joined_at": "2024-01-01T00:00:00Z",
        }
        for i in range(48)
    ]
    agents = [
        {"id": opaque(), "kind": "scripted_service", "runtime": "northstar-fixture/v1"}
        for _ in range(12)
    ]
    organization = {
        "people": people,
        "agents": agents,
        "repositories": [
            f"northstar/{x}" for x in ("atlas", "orion", "lyra", "gateway", "sdk", "research")
        ],
        "projects": ["atlas", "orion", "lyra"],
        "customers": ["Aster", "Birch", "Cedar", "Dune"],
        "vendors": ["Lumen", "Harbor", "Quartz"],
        "compartments": ["customer", "research"],
        "identity_evidence": "synthetic authoritative directory; no real identity verification",
    }
    events, labels, investigations, artifacts = [], [], [], []
    base = datetime(2026, 7, 13, 12, tzinfo=UTC)
    for replica in range(replicas):
        for spec in SPECS:
            for family in range(2):
                for variant in VARIANTS:
                    investigation = opaque()
                    investigations.append({"id": investigation, "audit_area": spec.id})
                    subject = people[rng.randrange(len(people))]
                    peer = people[(people.index(subject) + 1) % len(people)]["id"]
                    agent = agents[rng.randrange(len(agents))]["id"]
                    material_nonce = opaque()
                    if spec.id == "information-exposure":
                        artifact_bytes = (
                            "customer_id,name,fixture_only\n"
                            f"Aster-{material_nonce},Invented Person,true\n"
                            if family
                            else f"NS_FIXTURE_KEY_{material_nonce}_NOT_A_CREDENTIAL"
                        ).encode()
                    elif spec.id in ("agent-reconciliation", "license-provenance"):
                        artifact_bytes = (
                            f"# Northstar invented source fixture {material_nonce}\n"
                            "def normalize(value):\n    return str(value).strip()\n"
                        ).encode()
                    else:
                        artifact_bytes = ("Northstar project material " + material_nonce).encode()
                    artifact = hashlib.sha256(artifact_bytes).hexdigest()
                    artifacts.append(
                        {
                            "id": artifact,
                            "version": 1,
                            "sha256": artifact,
                            "bytes_utf8": artifact_bytes.decode(),
                            "owner": subject["id"],
                            "project": ("atlas", "orion", "lyra")[family],
                        }
                    )
                    revision_bytes = (artifact + ":revision-2").encode()
                    artifacts.append(
                        {
                            "id": hashlib.sha256(revision_bytes).hexdigest(),
                            "version": 2,
                            "parent_ids": [artifact],
                            "sha256": hashlib.sha256(revision_bytes).hexdigest(),
                            "bytes_utf8": revision_bytes.decode(),
                            "owner": subject["id"],
                            "project": ("atlas", "orion", "lyra")[family],
                        }
                    )
                    center = base + timedelta(days=rng.randrange(110), minutes=rng.randrange(360))

                    def at(minutes, center=center):
                        return (
                            (center + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
                        )

                    positive = variant not in ("compliant", "benign")
                    facts = _facts(
                        spec.id,
                        family,
                        positive,
                        variant == "benign",
                        (subject["id"], peer, agent, artifact),
                        at,
                        [p["id"] for p in people if p["role"] == "engineer"],
                    )
                    counts = dict(Counter(t for t, _ in facts))
                    facts.append(
                        (
                            "collector.snapshot",
                            {
                                "complete": True,
                                "permission_denied": False,
                                "loss": False,
                                "counts": counts,
                                "opening_checkpoint": opaque(),
                                "closing_checkpoint": opaque(),
                                "excluded_channels": [],
                                "inventory_independent": True,
                            },
                        )
                    )
                    case_events = []
                    for kind, payload in facts:
                        event_id, native_id = opaque(), opaque()
                        source = SOURCES[kind]
                        attrs = {
                            **payload,
                            "audit_area": spec.id,
                            "investigation_id": investigation,
                            "identity_resolved": True,
                            "clock_quality": "exact",
                        }
                        native = _native(
                            source,
                            native_id,
                            subject["identities"].get(source, subject["id"]),
                            at(0),
                            kind,
                            payload,
                        )
                        event = {
                            "id": event_id,
                            "source": source,
                            "native_id": native_id,
                            "actor": subject["id"],
                            "agent_id": agent,
                            "session_id": investigation,
                            "campaign_id": investigation
                            if spec.id.startswith("campaign")
                            else None,
                            "project_id": ("atlas", "orion", "lyra")[family],
                            "event_type": kind,
                            "occurred_at": payload.get("at", at(0)),
                            "ingested_at": at(120),
                            "artifact_ids": [artifact],
                            "content": "Northstar project record.",
                            "attributes": attrs,
                            "classification": "internal",
                            "provenance": {
                                "adapter": f"{source}-fixture/v1",
                                "native_bytes": _json(native),
                                "assurance": "synthetic-commitment-only",
                            },
                        }
                        case_events.append(seal(event))
                    decisive = next(e for e in case_events if e["event_type"] == spec.types[0])
                    if variant == "ambiguous":
                        decisive["attributes"]["identity_resolved"] = False
                        seal(decisive)
                    elif variant == "contradictory":
                        # Preserve both independently collected versions, no latest-wins.
                        duplicate = json.loads(_json(decisive))
                        duplicate["id"] = opaque()
                        duplicate["content"] = "Northstar project record revised."
                        field = FIELD_CONTRACTS[spec.id][spec.types[0]][0]
                        value = duplicate["attributes"][field]
                        if field == "person":
                            replacement = peer if value != peer else subject["id"]
                        elif field in {"artifact", "hash", "fingerprint", "output_hash"}:
                            replacement = hashlib.sha256(revision_bytes).hexdigest()
                        elif isinstance(value, bool):
                            replacement = not value
                        elif isinstance(value, list):
                            replacement = [] if value else [decisive["native_id"]]
                        elif isinstance(value, (float, int)):
                            replacement = value + 1
                        else:
                            replacement = hashlib.sha256(_json(value).encode()).hexdigest()
                        duplicate["attributes"][field] = replacement
                        decisive["provenance"]["native_revision"] = "1"
                        duplicate["provenance"]["native_revision"] = "2"
                        seal(decisive)
                        duplicate["provenance"]["native_bytes"] = _json(
                            {
                                "revision_of": decisive["native_id"],
                                "revision": 2,
                                "record": duplicate["attributes"],
                            }
                        )
                        case_events.append(seal(duplicate))
                    elif variant == "missing":
                        case_events.remove(decisive)
                    elif variant == "tampered":
                        decisive["provenance"]["native_bytes"] += " "
                    elif variant == "permissions":
                        case_events.remove(decisive)
                        snapshot = next(
                            e for e in case_events if e["event_type"] == "collector.snapshot"
                        )
                        snapshot["attributes"]["complete"] = False
                        snapshot["attributes"]["permission_denied"] = True
                        seal(snapshot)
                    events.extend(case_events)
                    if include_truth:
                        labels.append(
                            {
                                "investigation_id": investigation,
                                "audit_id": spec.id,
                                "family": spec.families[family],
                                "variant": variant,
                                "replica": replica,
                                "world_truth": {
                                    "mechanism_discrepancy": positive,
                                    "facts_before_collection": json.loads(_json(facts[:-1])),
                                    "scripted_intervention": True,
                                },
                                "expected_supportable": "supported"
                                if variant == "supported"
                                else (
                                    "no_finding"
                                    if variant in ("compliant", "benign")
                                    else "insufficient_evidence"
                                ),
                                "evidence_ids": [e["id"] for e in case_events],
                            }
                        )
    rng.shuffle(events)
    partitions = []
    for start in range(0, len(events), 4096):
        batch = events[start : start + 4096]
        partitions.append(
            {
                "offset": start,
                "count": len(batch),
                "sha256": hashlib.sha256(_json(batch).encode()).hexdigest(),
            }
        )
    manifest = {
        "sources": sorted({e["source"] for e in events}),
        "coverage": {
            "start": "2026-07-13T00:00:00Z",
            "end": "2026-11-02T23:59:59Z",
            "event_count": len(events),
            "investigation_count": len(investigations),
            "native_completeness": "unvalidated",
            "universe": {
                "accounts": [p["id"] for p in people],
                "repositories": organization["repositories"],
                "period_weeks": 16,
            },
            "loss": False,
        },
        "integrity": "synthetic-hash-commitments-not-native-signatures",
        "partitions": partitions,
        "resources": {
            "organization": organization,
            "investigations": investigations,
            "artifacts": artifacts,
        },
        "generator": "northstar/v1",
        "execution_kind": "scripted_fixture",
        "assurance": "Synthetic source-shaped records; no Tinfoil or real model execution.",
    }
    dataset = {
        "id": "northstar",
        "title": "Northstar Systems",
        "origin": "synthetic",
        "manifest": manifest,
        "events": events,
    }
    truth = (
        {"version": "northstar-truth/v1", "seed": seed, "cases": labels} if include_truth else None
    )
    return dataset, truth


def verify_partitions(dataset):
    """Verify whole snapshot coverage, order, cardinality and partition bytes."""
    offset = 0
    for part in dataset["manifest"]["partitions"]:
        if part["offset"] != offset or part["count"] <= 0:
            raise ValueError("partition gap, overlap or empty partition")
        batch = dataset["events"][offset : offset + part["count"]]
        if (
            len(batch) != part["count"]
            or hashlib.sha256(_json(batch).encode()).hexdigest() != part["sha256"]
        ):
            raise ValueError("partition commitment mismatch")
        offset += part["count"]
    if offset != len(dataset["events"]) or offset != dataset["manifest"]["coverage"]["event_count"]:
        raise ValueError("uncommitted or missing events")
    return True
