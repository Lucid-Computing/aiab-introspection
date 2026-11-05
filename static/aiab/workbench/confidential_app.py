"""Confidential supervisor. No provisioning, keys, or authority are delegated to Pi.

A signed deployment gate is necessary but not itself hardware attestation. The
independent collector verifies the running release before producing a submission.
The environment factory has no test mode and never trusts HTTP runtime-facts.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from fastapi import HTTPException, Request

from .contracts import EVENT_FIELDS, dataset_digest, plan_digest, timestamp, validate_plan
from .governance import GovernanceStore, canonical, commitment
from .model import MODEL_PROFILE
from .runner import execute_plan
from .service import Principal, SealedInputs, create_app

PROFILE = "aiab-confidential/deployment/v1"
SUBMISSION = "aiab-confidential/collector/v1"
TOKEN = "aiab-confidential/principal/v1"
RULE = "aiab-confidential/event-boundary/v1"


def b64(raw):
    return base64.b64encode(raw).decode()


def sign(payload, key):
    return {"payload": payload, "signature": b64(key.sign(canonical(payload)))}


def verify(document, public_key, domain):
    if set(document) != {"payload", "signature"} or document["payload"]["domain"] != domain:
        raise ValueError("wrong signed domain")
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True)).verify(
        base64.b64decode(document["signature"], validate=True), canonical(document["payload"])
    )
    return document["payload"]


def fresh(payload):
    # Short-lived credentials/profile MUST expire; infinity/null is not a measurement.
    expiry = payload.get("expires_at")
    if type(expiry) not in (int, float) or not time.time() < expiry < time.time() + 86400:
        raise ValueError("missing or stale assurance")


def input_manifest(dataset, rule, coverage):
    """Complete normalized bytes, method, observed universe and source provenance binding."""
    return {
        "dataset_id": dataset["id"],
        "dataset_digest": dataset_digest(dataset),
        "manifest": dataset["manifest"],
        "aggregate_rule": rule,
        "subject_coverage": coverage,
    }


def check_input(plan, dataset, entry):
    validate_plan(plan)
    if plan["mode"] != "confidential" or plan["agent"]["provider"] != "tinfoil":
        raise ValueError("confidential profile required")
    if plan["agent"]["model"] != MODEL_PROFILE["id"]:
        raise ValueError("unapproved model")
    for step in plan["steps"]:
        if step["kind"] == "agent" and step["config"].get("model_profile") != MODEL_PROFILE:
            raise ValueError("unapproved model profile")
    manifest = entry["dataset_manifest"]
    if (
        input_manifest(dataset, manifest["aggregate_rule"], manifest["subject_coverage"])
        != manifest
    ):
        raise ValueError("input substitution")
    scope, grant = plan["scope"], entry["source_permissions"]
    if set(scope["dataset_ids"]) != {dataset["id"]}:
        raise ValueError("one exact dataset required")
    if scope != grant["scope"] or not scope["required_sources"] or not scope["fields"]:
        raise ValueError("unresolved source permissions")
    if not {
        "actor",
        "id",
        "source",
        "event_type",
        "classification",
        "attributes",
        "provenance",
    } <= set(scope["fields"]):
        raise ValueError("aggregate requires explicit fields")
    if set(scope["fields"]) - set(EVENT_FIELDS):
        raise ValueError("unknown fields")
    # Reject overbroad inputs, including mixed/unknown subjects; no silent attribution.
    for event in dataset["events"]:
        if (
            event["actor"] not in scope["subjects"]
            or event["source"] not in scope["required_sources"]
        ):
            raise ValueError("source or subject outside permission")
        moment = timestamp(event["occurred_at"]) if event["occurred_at"] else None
        for bound, lower in ((scope["time_start"], True), (scope["time_end"], False)):
            if bound and (
                moment is None
                or (moment < timestamp(bound) if lower else moment > timestamp(bound))
            ):
                raise ValueError("event outside time permission")
        if event["provenance"].get("loss") or not event["native_id"]:
            raise ValueError("lossy or unattributed event")
    if set(manifest["subject_coverage"]) != set(scope["subjects"]):
        raise ValueError("coverage population mismatch")
    for subject in scope["subjects"]:
        coverage = manifest["subject_coverage"][subject]
        ids = sorted(e["id"] for e in dataset["events"] if e["actor"] == subject)
        if set(coverage) != {"event_ids", "complete"} or type(coverage["complete"]) is not bool:
            raise ValueError("invalid coverage")
        if coverage["event_ids"] != ids:
            raise ValueError("coverage provenance mismatch")
    return manifest


def subject_result(plan, dataset, manifest):
    """Recompute typed attribution from authorized evidence, never model prose/details.

    Completeness is a source-authority signed assertion, not inferred from hashes.
    No claim of semantic validity or real-world collection completeness is made.
    """
    rule = manifest["aggregate_rule"]
    if (
        set(rule) != {"version", "event_type", "classification", "forbidden_destinations"}
        or rule["version"] != RULE
        or not isinstance(rule["event_type"], str)
        or not isinstance(rule["classification"], str)
        or not isinstance(rule["forbidden_destinations"], list)
        or not rule["forbidden_destinations"]
        or not all(isinstance(x, str) and x for x in rule["forbidden_destinations"])
    ):
        raise ValueError("unsupported aggregate rule")
    findings = []
    for subject in sorted(plan["scope"]["subjects"]):
        events = [e for e in dataset["events"] if e["actor"] == subject]
        support = sorted(
            e["id"]
            for e in events
            if e["event_type"] == rule["event_type"]
            and e["classification"] == rule["classification"]
            and e["attributes"].get("destination") in rule["forbidden_destinations"]
        )
        complete = manifest["subject_coverage"][subject]["complete"]
        outcome = "supported" if support else "no_finding" if complete else "insufficient_evidence"
        findings.append(
            {
                "subject": subject,
                "outcome": outcome,
                "evidence_ids": support,
                "universe_ids": sorted(e["id"] for e in events),
            }
        )
    return {
        "status": "completed",
        "findings": findings,
        "coverage": {"complete": all(c["complete"] for c in manifest["subject_coverage"].values())},
    }


class ReceiptStore(GovernanceStore):
    """External provisioned supervisor key; never send private bytes to agent workers."""

    def __init__(self, path, key):
        self.signing_key = key
        super().__init__(path)

    def _key(self, db):
        return self.signing_key


class ConfidentialRuntime:
    def __init__(
        self,
        *,
        bundle,
        authority_key,
        private_dir,
        input_key,
        result_key,
        receipt_key,
        test_attestation=False,
        execute=execute_plan,
    ):
        if execute is not execute_plan and not test_attestation:
            raise ValueError("execution injection is test-only")
        if len(result_key) != 32:
            raise ValueError("invalid result key")
        self.profile = verify(bundle, authority_key, PROFILE)
        fresh(self.profile)
        p = self.profile
        if p["assurance"] != ("test-attestation" if test_attestation else "signed-deployment-gate"):
            raise ValueError("test/live assurance mismatch")
        required = {
            "measured_image",
            "protected_storage",
            "no_ui_plaintext",
            "sandbox_review",
            "collector_review",
            "source_authority_review",
        }
        if set(p["gates"]) != required or any(
            not isinstance(v, str) or not v for v in p["gates"].values()
        ):
            raise ValueError("independent gate evidence missing")
        if not test_attestation and any(
            re.fullmatch(r"sha256:[a-f0-9]{64}", v) is None for v in p["gates"].values()
        ):
            raise ValueError("live gates require reviewed evidence digests")
        if p["model_profile"] != MODEL_PROFILE or not re.fullmatch(
            r"sha256:[a-f0-9]{64}", p["release_digest"]
        ):
            raise ValueError("unapproved deployment")
        if p["input_public_key"] != b64(input_key.public_key().public_bytes_raw()):
            raise ValueError("input key binding mismatch")
        if p["receipt_public_key"] != b64(receipt_key.public_key().public_bytes_raw()):
            raise ValueError("receipt key binding mismatch")
        self.input_key, self.result_key = input_key, result_key
        self.directory = Path(private_dir)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.store = ReceiptStore(self.directory / "governance.sqlite", receipt_key)
        # A changed signed configuration cannot reset domains, population or release history.
        with self.store._db() as db:
            frozen = self.store._get(db, "deployment", "immutable", False)
            config = {k: p[k] for k in ("identities", "rosters", "policies", "plans", "collectors")}
            if frozen is not None and frozen != config:
                raise ValueError("provisioned authority material is immutable")
            self.store._put(db, "deployment", "immutable", config)
        self.identities = {i["id"]: i for i in p["identities"]}
        if len(self.identities) != len(p["identities"]):
            raise ValueError("duplicate identity")
        for i in p["identities"]:
            self.store.enroll_identity(i["id"], i["public_key"], roles=i["roles"])
        for r in p["rosters"]:
            self.store.freeze_roster(**r)
        for policy in p["policies"]:
            self.store.configure_release_policy(**policy)
        self.entries = {}
        for entry in p["plans"]:
            # Each exact source grant is separately signed by an enrolled source authority.
            source = self.identities[entry["source_authority"]]
            if "source_authority" not in source["roles"] or source["id"] == entry["owner"]:
                raise ValueError("independent source authority required")
            permissions = verify(
                entry["source_grant"], source["public_key"], "aiab-confidential/source/v1"
            )
            fresh(permissions)
            for name in ("plan_digest", "dataset_manifest", "authorization", "scope"):
                expected = (
                    plan_digest(entry["plan"])
                    if name == "plan_digest"
                    else entry["plan"]["scope"]
                    if name == "scope"
                    else entry[name]
                )
                if permissions[name] != expected:
                    raise ValueError("source grant binding mismatch")
            entry = {**entry, "source_permissions": permissions}
            registration = self.store.register_plan(
                entry["plan"],
                entry["owner"],
                dataset_manifest=entry["dataset_manifest"],
                authorization=entry["authorization"],
                roster_id=entry["roster_id"],
            )
            self.entries[registration["digest"]] = entry
        self.execute_private = execute
        self.profile_digest = commitment(PROFILE, p)

    def authenticate(self, token):
        encoded, signature = token.split(".")
        payload = json.loads(base64.b64decode(encoded, validate=True))
        identity = self.identities[payload["actor"]]
        verify({"payload": payload, "signature": signature}, identity["public_key"], TOKEN)
        fresh(payload)
        if payload["audience"] != self.profile_digest:
            raise ValueError("token audience mismatch")
        return Principal(identity["id"], "owner", frozenset(identity["capabilities"]))

    def entry_for_run(self, run_id):
        with self.store._db() as db:
            run = self.store._get(db, "run", run_id)
        return self.entries[run["plan_digest"]]

    def admit(self, who, plan, dataset, *, submission, request_id):
        fresh(self.profile)
        digest = plan_digest(plan)
        entry = self.entries[digest]
        manifest = check_input(plan, dataset, entry)
        fresh(entry["source_permissions"])
        attestation = submission["collector"]
        collector_id = attestation["payload"]["collector"]
        if collector_id not in self.profile["collectors"]:
            raise ValueError("unapproved collector")
        ticket = verify(attestation, self.identities[collector_id]["public_key"], SUBMISSION)
        fresh(ticket)
        expected = {
            "principal": who.id,
            "request_id": request_id,
            "plan_digest": digest,
            "input_digest": dataset_digest(dataset),
            "profile_digest": self.profile_digest,
            "input_public_key": self.profile["input_public_key"],
            "assurance": self.profile["assurance"],
        }
        if any(ticket.get(k) != v for k, v in expected.items()):
            raise ValueError("collector binding mismatch")
        # Compatibility assertions for GovernanceStore, produced ONLY after the
        # independently signed gate + trusted collector ticket have verified.
        facts = {
            k: True
            for k in (
                "attestation_verified",
                "sealed_input_verified",
                "client_verified",
                "plaintext_ui_absent",
                "protected_execution_verified",
            )
        }
        facts.update(
            runtime_digest=self.profile["release_digest"],
            plan_digest=digest,
            input_commitment=commitment("aiab-governance/dataset-manifest/v1", manifest),
            expires_at=min(self.profile["expires_at"], ticket["expires_at"]),
        )
        admitted = self.store.admit(
            plan,
            who.id,
            manifest,
            request_id=request_id,
            authorization=entry["authorization"],
            runtime_facts=facts,
        )
        # Persist only encrypted input needed for independent recomputation after restart.
        nonce = secrets.token_bytes(12)
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        encrypted = nonce + AESGCM(self.result_key).encrypt(
            nonce, canonical(dataset), admitted["run_id"].encode()
        )
        with self.store._db() as db:
            self.store._put(
                db, "sealed_dataset", admitted["run_id"], {"ciphertext": b64(encrypted)}
            )
        return {
            "id": who.id,
            "authorized_dataset_ids": entry["authorization"]["dataset_ids"],
            "authorized_subjects": entry["authorization"]["subjects"],
            "governance_run_id": admitted["run_id"],
        }

    def revalidate(self, run_id):
        fresh(self.profile)
        fresh(self.entry_for_run(run_id)["source_permissions"])
        self.store.revalidate(run_id)

    async def execute(self, plan, dataset, *, actor):
        run_id = actor["governance_run_id"]
        self.revalidate(run_id)

        async def event(_private_event):
            self.revalidate(run_id)

        # Private runner output never controls aggregate attribution or free-text release.
        await self.execute_private(plan, dataset, actor=actor, on_event=event)
        self.revalidate(run_id)
        return subject_result(plan, dataset, self.entry_for_run(run_id)["dataset_manifest"])

    def can_release(self, who, run_id):
        self.revalidate(run_id)
        return who.id in {
            r["id"] for r in self.entry_for_run(run_id)["plan"]["disclosure"]["recipients"]
        }

    def release(self, who, run_id, result):
        self.revalidate(run_id)
        entry = self.entry_for_run(run_id)
        with self.store._db() as db:
            record = self.store._get(db, "plan", plan_digest(entry["plan"]))
            blob = base64.b64decode(
                self.store._get(db, "sealed_dataset", run_id)["ciphertext"], validate=True
            )
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        dataset = json.loads(AESGCM(self.result_key).decrypt(blob[:12], blob[12:], run_id.encode()))
        manifest = check_input(entry["plan"], dataset, entry)
        expected = subject_result(entry["plan"], dataset, manifest)
        if canonical(result) != canonical(expected):
            raise ValueError("independent subject attribution mismatch")
        return self.store.release(
            run_id,
            expected,
            who.id,
            actor=who.id,
            authorization=entry["authorization"],
            validation={
                "aggregate_subjects_verified": True,
                "binding_digest": record["binding_digest"],
                "result_digest": commitment("aiab-governance/result/v1", expected),
            },
        )

    def app(self):
        app = create_app(
            authenticate=self.authenticate,
            admit=self.admit,
            release=self.release,
            ledger_path=str(self.directory / "runtime.sqlite"),
            result_key=self.result_key,
            sealed_inputs=SealedInputs(self.input_key),
            max_concurrent=1,
            execute=self.execute,
            cancel_run=lambda who, run_id: self.store.cancel(run_id, who.id),
            submission_context=True,
            authorize_release=self.can_release,
        )

        @app.get("/readyz")
        async def ready():
            try:
                fresh(self.profile)
                for entry in self.entries.values():
                    fresh(entry["source_permissions"])
                self.store.verify_ledger()
            except Exception:
                raise HTTPException(503, "runtime_unavailable") from None
            return {"ready": True, "assurance": self.profile["assurance"]}

        @app.get("/v1/deployment")
        async def deployment():
            # Signed public agreement only; source manifests can be private.
            return {
                "profile_digest": self.profile_digest,
                "input_public_key": self.profile["input_public_key"],
                "receipt_public_key": self.profile["receipt_public_key"],
                "release_digest": self.profile["release_digest"],
                "assurance": self.profile["assurance"],
            }

        @app.post("/v1/consent")
        async def consent(request: Request):
            # Signatures authenticate consent; HTTP supplies no role/authority.
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 16384:
                    raise HTTPException(413, "payload_too_large")
            try:
                document = json.loads(raw)
                payload = document["payload"]
                identity = self.identities[payload["actor"]]
                verify(document, identity["public_key"], payload["domain"])
                if payload["domain"] == "aiab-governance/approval/v1":
                    expected = self.store.approval_payload(
                        payload["plan_digest"],
                        payload["actor"],
                        expires_at=payload["expires_at"],
                        nonce=payload["nonce"],
                    )
                    if payload != expected:
                        raise ValueError("approval substitution")
                    self.store.approve(
                        payload["plan_digest"],
                        payload["actor"],
                        signature=document["signature"],
                        nonce=payload["nonce"],
                        expires_at=payload["expires_at"],
                    )
                elif payload["domain"] in {
                    "aiab-confidential/revoke/v1",
                    "aiab-confidential/revoke-source/v1",
                }:
                    if payload["profile_digest"] != self.profile_digest:
                        raise ValueError("wrong deployment")
                    # Revocation is monotonic/idempotent: replay cannot restore permission.
                    if payload["domain"].endswith("revoke-source/v1"):
                        self.store.invalidate_authorization(
                            payload["plan_digest"], actor=payload["actor"]
                        )
                    else:
                        self.store.revoke(payload["plan_digest"], payload["actor"])
                else:
                    raise ValueError("wrong consent domain")
            except Exception:
                raise HTTPException(403, "consent_denied") from None
            return {"accepted": True}

        return app


def factory():
    """Only production entrypoint. Trust root is baked into measured image."""
    private = Path("/run/aiab-private")
    root = Path("/app/deploy/authority.pub").read_text().strip()
    return ConfidentialRuntime(
        bundle=json.loads((private / "deployment.json").read_text()),
        authority_key=root,
        private_dir="/private",
        input_key=X25519PrivateKey.from_private_bytes((private / "input.key").read_bytes()),
        result_key=(private / "result.key").read_bytes(),
        receipt_key=Ed25519PrivateKey.from_private_bytes((private / "receipt.key").read_bytes()),
    ).app()
