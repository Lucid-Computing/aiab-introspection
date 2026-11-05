"""Security boundary tests; stand-in only for evidence-owned contract integration."""

import base64
import json
import sqlite3
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aiab.workbench import governance as g


@pytest.fixture(autouse=True)
def contract_adapter(monkeypatch):
    # Until the evidence agent's contract lands, a deliberately narrow fixture
    # supplies digest/validation only. Do not create/overwrite its owned module.
    try:
        __import__("aiab.workbench.contracts")
    except ModuleNotFoundError:
        module = types.ModuleType("aiab.workbench.contracts")
        module.validate_plan = lambda value: value
        module.plan_digest = lambda value: g.commitment("test-only/plan", value)
        monkeypatch.setitem(sys.modules, "aiab.workbench.contracts", module)


def plan(mode="confidential", subjects=None, owner=None, recipients=None):
    subjects = subjects or (["alice"] if mode == "personal" else ["alice", "bob"])
    owner = owner or ("alice" if mode == "personal" else "employer")
    return {
        "version": "aiab-workbench/v1",
        "id": "audit",
        "title": "Boundary audit",
        "question": "Did data cross the agreed boundary?",
        "criteria": [{"id": "policy", "description": "Compare events to policy"}],
        "mode": mode,
        "scope": {
            "dataset_ids": ["data"],
            "subjects": subjects,
            "time_start": None,
            "time_end": None,
            "fields": [],
            "required_sources": [],
        },
        "steps": [
            {
                "id": "check",
                "kind": "rules",
                "title": "Check",
                "inputs": [],
                "config": {"rule": "boundary"},
            }
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
            "recipients": recipients
            or [
                {
                    "id": owner,
                    "role": "owner",
                    "fields": ["counts", "coverage", "status"],
                    "allow_evidence": False,
                    "allow_excerpts": False,
                }
            ],
            "min_group_size": 2,
            "max_releases": 3,
            "query_family": "policy",
        },
        "approval": {
            "required_signers": [],
            "roster": subjects if mode == "confidential" else [],
            "expires_at": None,
        },
    }


class Setup:
    def __init__(self, path, mode="confidential", *, subjects=None, recipients=None):
        self.path = path
        self.store = g.GovernanceStore(path)
        self.keys = {
            who: Ed25519PrivateKey.generate()
            for who in ("alice", "bob", "employer", "authority", "reviewer", "outsider")
        }
        for who, key in self.keys.items():
            roles = {
                "authority": ["roster_authority", "release_authority", "source_authority"],
                "employer": ["employer"],
                "reviewer": ["challenge_reviewer"],
            }.get(who, [])
            self.store.enroll_identity(
                who, base64.b64encode(key.public_key().public_bytes_raw()).decode(), roles=roles
            )
        self.plan = plan(mode, subjects=subjects, recipients=recipients)
        self.owner = "alice" if mode == "personal" else "employer"
        self.manifest = {"id": "data", "snapshot": "v1", "subjects": self.plan["scope"]["subjects"]}
        self.auth = {
            "dataset_ids": ["data"],
            "subjects": self.plan["scope"]["subjects"],
            "privacy_domain": "organization",
            "grant_id": "grant-1",
            "expires_at": None,
        }
        self.store.freeze_roster(
            "roster",
            ["alice", "bob"],
            actor="authority",
            source_commitment="independent-directory-v1",
            scope_id="organization",
        )
        self.store.configure_release_policy(
            "organization",
            actor="authority",
            max_releases=5,
            min_group_size=2,
            families={
                "policy": {
                    "subjects": self.auth["subjects"],
                    "fields": ["counts", "coverage", "status", "findings"],
                    "query_digest": g.query_digest(self.plan),
                }
            },
        )
        self.reg = self.register()
        self.digest = self.reg["digest"]

    def register(self):
        return self.store.register_plan(
            self.plan,
            self.owner,
            dataset_manifest=self.manifest,
            authorization=self.auth,
            roster_id="roster",
        )

    def approve(self, who, **overrides):
        nonce = overrides.pop("nonce", "signature-" + who)
        expiry = overrides.pop("expires_at", None)
        payload = self.store.approval_payload(self.digest, who, nonce=nonce, expires_at=expiry)
        signature = base64.b64encode(self.keys[who].sign(g.canonical(payload))).decode()
        return self.store.approve(
            self.digest, who, signature=signature, nonce=nonce, expires_at=expiry, **overrides
        )

    def all_approve(self):
        for who in sorted(
            set(
                [self.owner]
                + self.plan["scope"]["subjects"]
                + self.plan["approval"]["required_signers"]
            )
        ):
            self.approve(who)

    def facts(self):
        return {
            "attestation_verified": True,
            "sealed_input_verified": True,
            "client_verified": True,
            "plaintext_ui_absent": True,
            "protected_execution_verified": True,
            "runtime_digest": "measured-runtime-v1",
            "input_commitment": g.commitment("aiab-governance/dataset-manifest/v1", self.manifest),
            "plan_digest": self.digest,
            "expires_at": g._now() + 600,
        }

    def admit(self, request_id="request-1", **kwargs):
        return self.store.admit(
            self.plan,
            self.owner,
            self.manifest,
            request_id=request_id,
            authorization=self.auth,
            runtime_facts=kwargs.pop("runtime_facts", self.facts()),
            **kwargs,
        )["run_id"]

    def result(self, outcomes=None):
        return {
            "findings": [
                {"id": "secret-subject-id", "outcome": o, "raw": "SECRET", "trace": ["SECRET"]}
                for o in (outcomes or ["no_finding"] * len(self.auth["subjects"]))
            ],
            "coverage": {"complete": True, "raw": "SECRET"},
            "status": "completed",
            "trace": ["SECRET"],
            "raw": "SECRET",
            "usage": {"raw": "SECRET"},
        }

    def release(self, run_id, result=None, actor=None):
        result = result or self.result()
        actor = actor or self.owner
        return self.store.release(
            run_id,
            result,
            actor,
            actor=actor,
            authorization=self.auth,
            validation={
                "aggregate_subjects_verified": True,
                "binding_digest": self.reg["binding_digest"],
                "result_digest": g.commitment("aiab-governance/result/v1", result),
            },
        )


@pytest.fixture
def setup(tmp_path):
    return Setup(tmp_path / "governance.sqlite")


def test_signatures_bind_identity_plan_input_and_expiry(setup):
    s = setup
    payload = s.store.approval_payload(s.digest, "alice", nonce="n")
    signature = base64.b64encode(s.keys["bob"].sign(g.canonical(payload))).decode()
    with pytest.raises(ValueError, match="Ed25519"):
        s.store.approve(s.digest, "alice", signature=signature, nonce="n")
    signature = base64.b64encode(s.keys["alice"].sign(g.canonical(payload))).decode()
    for changed in ({"nonce": "changed"}, {"expires_at": g._now() + 100}):
        args = {"nonce": "n", **changed}
        with pytest.raises(ValueError, match="Ed25519"):
            s.store.approve(s.digest, "alice", signature=signature, **args)
    with pytest.raises(ValueError, match="required signer"):
        s.store.approval_payload(s.digest, "outsider", nonce="x")
    s.store.approve(s.digest, "alice", signature=signature, nonce="n")
    with pytest.raises(ValueError, match="replay"):
        s.store.approve(s.digest, "alice", signature=signature, nonce="n")
    s.approve("employer")
    with pytest.raises(ValueError, match="approval not ready"):
        s.admit()
    assert s.store.readiness(s.digest, "employer") == {"ready": False}
    s.approve("bob")
    assert s.store.readiness(s.digest, "employer") == {"ready": True}
    bad = deepcopy(s.manifest)
    bad["snapshot"] = "substitution"
    with pytest.raises(ValueError, match="input authorization mismatch"):
        s.store.admit(
            s.plan, s.owner, bad, request_id="r", authorization=s.auth, runtime_facts=s.facts()
        )


def test_missing_identity_and_keys_cannot_be_rebound(setup):
    s = setup
    with pytest.raises(ValueError, match="immutable"):
        s.store.enroll_identity(
            "alice", base64.b64encode(s.keys["bob"].public_key().public_bytes_raw()).decode()
        )
    modified = deepcopy(s.plan)
    modified["approval"]["required_signers"] = ["unknown"]
    with pytest.raises(ValueError, match="identity"):
        s.store.register_plan(
            modified, s.owner, dataset_manifest=s.manifest, authorization=s.auth, roster_id="roster"
        )


def test_roster_cannot_shrink_and_subjects_must_match(setup):
    s = setup
    with pytest.raises(ValueError, match="shrinking"):
        s.store.freeze_roster(
            "new-roster",
            ["alice"],
            actor="authority",
            source_commitment="v2",
            scope_id="organization",
        )
    with pytest.raises(ValueError, match="authority"):
        s.store.freeze_roster(
            "fake", ["alice"], actor="employer", source_commitment="fake", scope_id="organization"
        )
    modified = deepcopy(s.plan)
    modified["scope"]["subjects"] = ["alice"]
    with pytest.raises(ValueError, match="full frozen roster"):
        s.store.register_plan(
            modified, s.owner, dataset_manifest=s.manifest, authorization=s.auth, roster_id="roster"
        )
    modified = deepcopy(s.plan)
    modified["approval"]["roster"] = ["alice"]
    with pytest.raises(ValueError, match="wrong plan roster"):
        s.store.register_plan(
            modified, s.owner, dataset_manifest=s.manifest, authorization=s.auth, roster_id="roster"
        )


def test_stale_policy_invalidates_pending_and_old_signatures(setup):
    s = setup
    s.all_approve()
    run_id = s.admit()
    old = deepcopy(s.plan)
    s.plan["title"] = "New version"
    new = s.register()
    with pytest.raises(ValueError, match="stale plan"):
        s.release(run_id)
    with pytest.raises(ValueError, match="historical plan"):
        s.store.register_plan(
            old, s.owner, dataset_manifest=s.manifest, authorization=s.auth, roster_id="roster"
        )
    assert s.store.readiness(new["digest"], "employer") == {"ready": False}


def test_revoked_midrun_blocks_release_even_after_reapproval(setup):
    s = setup
    s.all_approve()
    run_id = s.admit()
    s.store.revoke(s.digest, "bob")
    s.approve("bob", nonce="fresh")
    with pytest.raises(ValueError, match="blocked"):
        s.release(run_id)
    with pytest.raises(ValueError, match="blocked"):
        s.store.revalidate(run_id)


def test_expiry_rechecked_and_source_revocation(setup, monkeypatch):
    s = setup
    s.approve("alice", expires_at=g._now() + 10)
    s.approve("bob")
    s.approve("employer")
    run_id = s.admit()
    real_now = g._now()
    monkeypatch.setattr(g, "_now", lambda: real_now + 20)
    with pytest.raises(ValueError, match="approval not ready"):
        s.release(run_id)
    monkeypatch.setattr(g, "_now", lambda: real_now)
    s.store.invalidate_authorization(s.digest, actor="authority")
    with pytest.raises(ValueError, match="source authorization revoked"):
        s.release(run_id)


def test_confidential_mode_requires_actual_verified_boundary(setup):
    s = setup
    s.all_approve()
    for facts in (
        None,
        {},
        {**s.facts(), "plaintext_ui_absent": False},
        {**s.facts(), "runtime_digest": None},
        {**s.facts(), "plan_digest": "wrong"},
        {**s.facts(), "input_commitment": "wrong"},
    ):
        with pytest.raises(ValueError):
            s.admit(runtime_facts=facts)
    s.admit()


def test_restart_duplicate_overlap_and_budgets(setup):
    s = setup
    s.all_approve()
    run_id = s.admit()
    s.store = g.GovernanceStore(s.path)
    with pytest.raises(ValueError, match="duplicate"):
        s.admit()
    with pytest.raises(ValueError, match="overlapping"):
        s.admit("request-2")
    package = s.release(run_id)
    assert package["projection"]["counts"]["values"]["no_finding"] == 2
    s.store = g.GovernanceStore(s.path)
    with pytest.raises(ValueError, match="duplicate release"):
        s.release(run_id)


def test_parallel_admission_reserves_once(setup):
    s = setup
    s.all_approve()

    def attempt(index):
        store = g.GovernanceStore(s.path)
        try:
            return store.admit(
                s.plan,
                s.owner,
                s.manifest,
                request_id=f"request-{index}",
                authorization=s.auth,
                runtime_facts=s.facts(),
            )
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(attempt, range(12)))
    assert sum(v is not None for v in outcomes) == 1


def test_parallel_release_once_and_receipt_key_stable(setup):
    s = setup
    s.all_approve()
    run_id = s.admit()
    pinned = s.store.receipt_public_key()

    def attempt(_):
        store = g.GovernanceStore(s.path)
        result = s.result()
        try:
            return store.release(
                run_id,
                result,
                s.owner,
                actor=s.owner,
                authorization=s.auth,
                validation={
                    "aggregate_subjects_verified": True,
                    "binding_digest": s.reg["binding_digest"],
                    "result_digest": g.commitment("aiab-governance/result/v1", result),
                },
            )
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=6) as executor:
        packages = [p for p in executor.map(attempt, range(10)) if p]
    assert len(packages) == 1
    assert s.store.receipt_public_key() == pinned
    assert s.store.verify_receipt(packages[0], pinned)


def test_projections_never_copy_trace_raw_or_secret_ids(setup):
    s = setup
    s.all_approve()
    run_id = s.admit()
    package = s.release(run_id)
    encoded = json.dumps(package)
    assert "SECRET" not in encoded
    assert "secret-subject-id" not in encoded
    assert "trace" not in encoded and "raw" not in encoded
    assert s.store.run_status(run_id, "employer")["releases"] == [package]
    with pytest.raises(ValueError, match="not found"):
        s.store.run_status(run_id, "outsider")


def test_suppression_is_explicit_not_zero(setup):
    s = setup
    s.all_approve()
    package = s.release(s.admit(), s.result(["supported", "no_finding"]))
    assert package["projection"]["release_state"] == "suppressed"
    assert package["projection"]["counts"] == {"state": "suppressed", "value": None}
    assert "values" not in package["projection"]["counts"]


def test_unverified_aggregate_or_bad_recipient_never_releases(setup):
    s = setup
    s.all_approve()
    run_id = s.admit()
    for recipient, actor, validation in (
        ("employer", None, None),
        ("employer", "employer", None),
        ("outsider", "outsider", {}),
    ):
        with pytest.raises(ValueError):
            s.store.release(
                run_id,
                s.result(),
                recipient,
                actor=actor,
                authorization=s.auth,
                validation=validation,
            )
    s.release(run_id)


def test_receipts_tampering_binding_and_independent_checkpoint(setup):
    s = setup
    s.all_approve()
    package = s.release(s.admit())
    pinned = s.store.receipt_public_key()
    assert s.store.verify_receipt(package, pinned, expected={"plan_digest": s.digest})
    for altered in ("projection", "receipt"):
        bad = deepcopy(package)
        if altered == "projection":
            bad["projection"]["status"] = "tampered"
        else:
            bad["receipt"]["payload"]["recipient"] = "outsider"
        with pytest.raises(ValueError):
            s.store.verify_receipt(bad, pinned)
    with pytest.raises(ValueError):
        s.store.verify_receipt(package, pinned, expected={"run_id": "wrong"})
    checkpoint = s.store.checkpoint()
    assert s.store.verify_ledger(external_checkpoint=checkpoint)["externally_anchored"]
    with pytest.raises(ValueError, match="checkpoint"):
        s.store.verify_ledger(external_checkpoint={**checkpoint, "hash": "wrong"})
    with sqlite3.connect(s.path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="append only"):
            db.execute("UPDATE events SET hash='altered' WHERE seq=1")
        with pytest.raises(sqlite3.IntegrityError, match="append only"):
            db.execute("DELETE FROM events WHERE seq=1")


def test_personal_audits_undiscoverable_by_employer(tmp_path):
    s = Setup(tmp_path / "personal.sqlite", mode="personal")
    s.all_approve()
    run_id = s.admit()
    s.release(run_id)
    assert len(s.store.plans("alice")) == 1
    assert s.store.plans("employer") == []
    assert s.store.history("employer") == []
    for call in (
        lambda: s.store.history("employer", plan_digest=s.digest),
        lambda: s.store.readiness(s.digest, "employer"),
        lambda: s.store.run_status(run_id, "employer"),
    ):
        with pytest.raises(ValueError, match="not found"):
            call()


def test_employer_readiness_does_not_expose_refuser_or_timing(setup):
    s = setup
    before = s.store.history("employer")
    s.approve("alice")
    s.store.revoke(s.digest, "bob")
    assert s.store.history("employer") == before
    assert s.store.readiness(s.digest, "employer") == {"ready": False}
    assert "alice" not in json.dumps(before) and "bob" not in json.dumps(before)


def test_joint_requires_each_subject_consent(tmp_path):
    s = Setup(tmp_path / "joint.sqlite", mode="joint")
    s.approve("employer")
    s.approve("alice")
    with pytest.raises(ValueError, match="approval not ready"):
        s.admit()
    s.approve("bob")
    s.release(s.admit())


def test_challenge_disposition_lineage_and_visibility(tmp_path):
    recipients = [
        {
            "id": who,
            "role": "reviewer",
            "fields": ["counts"],
            "allow_evidence": False,
            "allow_excerpts": False,
        }
        for who in ("employer", "reviewer", "alice")
    ]
    s = Setup(tmp_path / "challenge.sqlite", recipients=recipients)
    s.all_approve()
    run_id = s.admit()
    s.release(run_id)
    root = s.store.challenge(
        run_id,
        "alice",
        claim_id="claim-1",
        correction_commitment="sha256:evidence",
        requested_remedy="review",
        recipients=["alice", "reviewer"],
    )
    child = s.store.challenge(
        run_id,
        "reviewer",
        claim_id="claim-1",
        correction_commitment="sha256:review",
        requested_remedy="review",
        recipients=["alice", "reviewer"],
        parent_id=root["id"],
        disposition="upheld",
    )
    assert child["parent_id"] == root["id"]
    assert s.store.challenges(run_id, "employer") == []
    assert s.store.challenges(run_id, "alice") == [root, child]
    for actor, kwargs in (
        ("employer", {"recipients": ["employer"]}),
        ("alice", {"recipients": ["alice"], "disposition": "upheld"}),
        ("reviewer", {"recipients": ["alice", "reviewer"], "claim_id": "other"}),
    ):
        args = {
            "claim_id": "claim-1",
            "correction_commitment": "hash",
            "requested_remedy": "review",
            "parent_id": root["id"],
            **kwargs,
        }
        with pytest.raises(ValueError):
            s.store.challenge(run_id, actor, **args)
    s.store = g.GovernanceStore(s.path)
    assert s.store.challenges(run_id, "alice")[0] == root


def test_fixed_policy_and_binding_cannot_be_changed(setup):
    s = setup
    with pytest.raises(ValueError, match="immutable"):
        s.store.configure_release_policy(
            "organization",
            actor="authority",
            max_releases=100,
            families={
                "policy": {
                    "subjects": ["alice", "bob"],
                    "fields": ["counts"],
                    "query_digest": g.query_digest(s.plan),
                }
            },
        )
    s.manifest["snapshot"] = "v2"
    with pytest.raises(ValueError, match="immutable"):
        s.register()


def test_query_semantics_and_family_fields_frozen(setup):
    s = setup
    changed = deepcopy(s.plan)
    changed["question"] = "Narrow down the individual?"
    with pytest.raises(ValueError, match="unapproved query semantics"):
        s.store.register_plan(
            changed, s.owner, dataset_manifest=s.manifest, authorization=s.auth, roster_id="roster"
        )
    changed = deepcopy(s.plan)
    changed["disclosure"]["recipients"][0]["fields"] = ["trace"]
    with pytest.raises(ValueError, match="recipient fields"):
        s.store.register_plan(
            changed, s.owner, dataset_manifest=s.manifest, authorization=s.auth, roster_id="roster"
        )


def test_new_plan_id_cannot_bypass_overlapping_reservation(setup):
    s = setup
    s.all_approve()
    s.admit()
    s.plan["id"] = "alternate-audit"
    s.reg = s.register()
    s.digest = s.reg["digest"]
    for who in ("alice", "bob", "employer"):
        s.approve(who, nonce="alternate-" + who)
    with pytest.raises(ValueError, match="overlapping"):
        s.admit("different-plan")


def test_cancel_does_not_refund_reservation(setup):
    s = setup
    s.all_approve()
    run_id = s.admit()
    s.store.cancel(run_id, s.owner)
    with pytest.raises(ValueError, match="blocked"):
        s.release(run_id)
    with pytest.raises(ValueError, match="overlapping"):
        s.admit("after-cancel")


def test_original_approval_expiry_cannot_be_extended_for_pending_run(setup, monkeypatch):
    s = setup
    now = g._now()
    s.approve("alice", expires_at=now + 10)
    s.approve("bob")
    s.approve("employer")
    run_id = s.admit()
    s.approve("alice", nonce="extended", expires_at=now + 1000)
    monkeypatch.setattr(g, "_now", lambda: now + 20)
    with pytest.raises(ValueError, match="original admission approval expired"):
        s.release(run_id)


def test_draft_binding_must_be_frozen_before_signing(setup):
    s = setup
    draft = deepcopy(s.plan)
    draft["id"] = "draft"
    registered = s.store.register_plan(draft, s.owner, roster_id="roster")
    with pytest.raises(ValueError, match="draft"):
        s.store.approval_payload(registered["digest"], "alice", nonce="draft-sign")
    registered = s.store.register_plan(
        draft, s.owner, roster_id="roster", dataset_manifest=s.manifest, authorization=s.auth
    )
    assert s.store.approval_payload(registered["digest"], "alice", nonce="bound-sign")


def test_confidential_runtime_expiry_revalidated_at_release(setup, monkeypatch):
    s = setup
    s.all_approve()
    run_id = s.admit()
    now = g._now()
    monkeypatch.setattr(g, "_now", lambda: now + 601)
    with pytest.raises(ValueError, match="freshness"):
        s.release(run_id)


def test_signed_aggregate_validation_is_exact_result_binding(setup):
    s = setup
    s.all_approve()
    run_id = s.admit()
    result = s.result()
    for validation in (
        {
            "aggregate_subjects_verified": True,
            "binding_digest": "wrong",
            "result_digest": g.commitment("aiab-governance/result/v1", result),
        },
        {
            "aggregate_subjects_verified": True,
            "binding_digest": s.reg["binding_digest"],
            "result_digest": "wrong",
        },
    ):
        with pytest.raises(ValueError, match="aggregate validation"):
            s.store.release(
                run_id, result, s.owner, actor=s.owner, authorization=s.auth, validation=validation
            )


def test_result_substitution_between_recipients_denied(tmp_path):
    recipients = [
        {
            "id": who,
            "role": "recipient",
            "fields": ["counts"],
            "allow_evidence": False,
            "allow_excerpts": False,
        }
        for who in ("alice", "employer")
    ]
    s = Setup(tmp_path / "multi.sqlite", recipients=recipients)
    s.all_approve()
    run_id = s.admit()
    s.release(run_id)
    with pytest.raises(ValueError, match="substitution"):
        s.release(run_id, s.result(["supported", "supported"]), actor="alice")
    s.release(run_id, actor="alice")


def test_corrected_release_cannot_link_unrelated_or_missing_output(setup):
    s = setup
    s.all_approve()
    run_id = s.admit()
    package = s.release(run_id)
    root = s.store.challenge(
        run_id,
        "alice",
        claim_id="claim",
        correction_commitment="hash",
        requested_remedy="correct",
        recipients=["alice", "employer"],
    )
    # The original result remains immutable, and cannot be labeled its own correction.
    with pytest.raises(ValueError):
        s.store.challenge(
            run_id,
            "employer",
            claim_id="claim",
            correction_commitment="hash2",
            requested_remedy="correct",
            recipients=["alice", "employer"],
            parent_id=root["id"],
            disposition="amended",
            corrected_release_id=package["receipt"]["payload"]["release_id"],
        )


def test_external_checkpoint_detects_actual_rollback(setup, tmp_path):
    s = setup
    snapshot = tmp_path / "snapshot.sqlite"
    with sqlite3.connect(s.path) as source, sqlite3.connect(snapshot) as dest:
        source.backup(dest)
    s.all_approve()
    pinned = s.store.checkpoint()
    restored = g.GovernanceStore(snapshot)
    assert restored.verify_ledger()["verified"]
    with pytest.raises(ValueError, match="rollback"):
        restored.verify_ledger(external_checkpoint=pinned)


def test_event_rewrite_even_with_triggers_removed_detected(setup):
    s = setup
    with sqlite3.connect(s.path) as db:
        db.execute("DROP TRIGGER events_no_update")
        db.execute("UPDATE events SET body='{}' WHERE seq=1")
    with pytest.raises(ValueError, match="altered event"):
        s.store.verify_ledger()


def test_plan_budget_reserved_before_any_disclosure(setup):
    s = setup
    s.plan["disclosure"]["max_releases"] = 1
    s.reg = s.register()
    s.digest = s.reg["digest"]
    s.all_approve()
    s.admit()
    with pytest.raises(ValueError, match="plan release budget exhausted"):
        s.admit("second")


def test_global_budget_across_disjoint_queries(tmp_path):
    s = Setup(tmp_path / "global-budget.sqlite", mode="joint", subjects=["alice"])
    second = deepcopy(s.plan)
    second["id"] = "bob-audit"
    second["scope"]["subjects"] = ["bob"]
    second["disclosure"]["query_family"] = "bob"
    s.store.configure_release_policy(
        "bounded",
        actor="authority",
        max_releases=1,
        families={
            "policy": {
                "subjects": ["alice"],
                "fields": ["counts", "coverage", "status"],
                "query_digest": g.query_digest(s.plan),
            },
            "bob": {
                "subjects": ["bob"],
                "fields": ["counts", "coverage", "status"],
                "query_digest": g.query_digest(second),
            },
        },
    )
    s.auth["privacy_domain"] = "bounded"
    s.plan["id"] = "bounded-audit"
    s.reg = s.register()
    s.digest = s.reg["digest"]
    s.all_approve()
    s.admit()
    s.plan = second
    s.auth["subjects"] = ["bob"]
    s.reg = s.register()
    s.digest = s.reg["digest"]
    s.approve("employer", nonce="second-employer")
    s.approve("bob", nonce="second-bob")
    with pytest.raises(ValueError, match="global release budget exhausted"):
        s.admit("disjoint-query")


def test_concurrent_first_open_uses_one_receipt_signer(tmp_path):
    path = tmp_path / "first-open.sqlite"
    with ThreadPoolExecutor(max_workers=8) as executor:
        keys = list(executor.map(lambda _: g.GovernanceStore(path).receipt_public_key(), range(16)))
    assert len(set(keys)) == 1


def test_exact_manifest_comparison_does_not_equate_booleans_and_integers(tmp_path):
    s = Setup(tmp_path / "canonical.sqlite", mode="joint")
    s.plan["id"] = "numeric-manifest"
    s.manifest["version"] = 1
    s.reg = s.register()
    s.digest = s.reg["digest"]
    s.all_approve()
    changed = deepcopy(s.manifest)
    changed["version"] = True  # Python equality treats these as equal; canonical JSON does not.
    with pytest.raises(ValueError, match="input authorization mismatch"):
        s.store.admit(s.plan, s.owner, changed, request_id="numeric-bypass", authorization=s.auth)


def test_signature_from_another_binding_rejected(setup):
    s = setup
    payload = s.store.approval_payload(s.digest, "alice", nonce="bound")
    payload["binding_digest"] = "wrong-dataset-or-roster"
    signature = base64.b64encode(s.keys["alice"].sign(g.canonical(payload))).decode()
    with pytest.raises(ValueError, match="Ed25519"):
        s.store.approve(s.digest, "alice", nonce="bound", signature=signature)


def test_amended_challenge_preserves_original_disposition_and_evidence(tmp_path):
    recipients = [
        {
            "id": who,
            "role": "recipient",
            "fields": ["counts"],
            "allow_evidence": False,
            "allow_excerpts": False,
        }
        for who in ("employer", "reviewer", "alice")
    ]
    s = Setup(tmp_path / "amended.sqlite", recipients=recipients)
    s.all_approve()
    run_id = s.admit()
    original_release = s.release(run_id)
    root = s.store.challenge(
        run_id,
        "alice",
        claim_id="claim",
        correction_commitment="original-evidence",
        requested_remedy="correct",
        recipients=["alice", "reviewer"],
    )
    amended = s.store.challenge(
        run_id,
        "reviewer",
        claim_id="claim",
        correction_commitment="corrected-evidence",
        requested_remedy="correct",
        recipients=["alice", "reviewer"],
        parent_id=root["id"],
        disposition="amended",
    )
    records = s.store.challenges(run_id, "alice")
    assert records == [root, amended]
    assert records[0]["correction_commitment"] == "original-evidence"
    assert records[0]["disposition"] is None
    assert s.store.run_status(run_id, s.owner)["releases"] == [original_release]


def test_new_database_is_private_by_default(tmp_path):
    path = tmp_path / "private.sqlite"
    g.GovernanceStore(path)
    assert path.stat().st_mode & 0o777 == 0o600


def test_source_grant_revocation_cannot_be_evaded_by_new_plan_version(setup):
    s = setup
    s.store.invalidate_authorization(s.digest, actor="authority")
    s.plan["id"] = "new-version-same-grant"
    with pytest.raises(ValueError, match="source authorization revoked"):
        s.register()
