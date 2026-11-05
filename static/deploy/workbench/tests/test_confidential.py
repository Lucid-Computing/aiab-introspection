"""Synthetic qualification only: real crypto/runner, explicitly TEST attestation."""

import asyncio
import time
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from aiab.workbench.confidential_app import (
    PROFILE,
    RULE,
    SUBMISSION,
    ConfidentialRuntime,
    b64,
    input_manifest,
    sign,
    subject_result,
)
from aiab.workbench.confidential_client import collect, seal, token
from aiab.workbench.contracts import EVENT_FIELDS, dataset_digest, plan_digest
from aiab.workbench.governance import GovernanceStore, query_digest


class TestAttestedConnection:
    """Does NOT perform hardware attestation. Production CLI cannot select this."""

    __test__ = False

    def __init__(self, app):
        self.app, self.calls, self.verified = app, [], False

    async def start(self):
        self.verified = True
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="https://TEST-runtime"
        )

    def check(self):
        assert self.verified

    async def request(self, method, path, body=None, token=None):
        self.check()
        self.calls.append((method, path, body))
        response = await self.client.request(
            method,
            path,
            content=body,
            headers={"authorization": "Bearer " + token} if token else {},
        )
        return response.status_code, response.json()

    async def close(self):
        await self.client.aclose()


def synthetic_fixture(tmp_path):
    keys = {
        name: Ed25519PrivateKey.generate()
        for name in (
            "TEST-root",
            "TEST-owner",
            "TEST-authority",
            "TEST-collector",
            "TEST-alice",
            "TEST-bob",
            "TEST-recipient",
        )
    }
    subjects = ["TEST-alice", "TEST-bob"]
    roles = {
        "TEST-authority": ["source_authority", "roster_authority", "release_authority"],
        "TEST-owner": ["employer"],
    }
    identities = [
        {
            "id": who,
            "public_key": b64(key.public_key().public_bytes_raw()),
            "roles": roles.get(who, []),
            "capabilities": [
                "runtime:submit",
                "runtime:status",
                "runtime:release",
                "runtime:cancel",
            ],
        }
        for who, key in keys.items()
        if who != "TEST-root"
    ]
    plan = {
        "version": "aiab-workbench/v1",
        "id": "TEST-audit",
        "title": "SYNTHETIC TEST ONLY",
        "question": "Did a classified synthetic event target a forbidden destination?",
        "criteria": [
            {"id": "boundary", "description": "exact event-boundary rule in bound manifest"}
        ],
        "mode": "confidential",
        "scope": {
            "dataset_ids": ["TEST-data"],
            "subjects": subjects,
            "fields": EVENT_FIELDS,
            "required_sources": ["TEST-source"],
            "time_start": None,
            "time_end": None,
        },
        "steps": [
            {"id": "select", "kind": "select", "title": "Select", "inputs": [], "config": {}},
            {
                "id": "release",
                "kind": "release",
                "title": "Release barrier",
                "inputs": ["select"],
                "config": {},
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
                    "id": who,
                    "role": "recipient",
                    "fields": ["counts", "status", "coverage"],
                    "allow_evidence": False,
                    "allow_excerpts": False,
                }
                for who in ["TEST-owner", "TEST-recipient"]
            ],
            "min_group_size": 2,
            "max_releases": 1,
            "query_family": "TEST-boundary",
        },
        "approval": {"roster": subjects, "required_signers": [], "expires_at": None},
    }
    events = []
    for index, subject in enumerate(subjects):
        event = {k: None for k in EVENT_FIELDS}
        event.update(
            id=f"TEST-event-{index}",
            source="TEST-source",
            native_id=str(index),
            actor=subject,
            event_type="transfer",
            classification="confidential",
            content="TEST-PRIVATE-SENTINEL",
            attributes={"destination": "forbidden"},
            provenance={"synthetic": True},
            artifact_ids=[],
        )
        events.append(event)
    data = {
        "id": "TEST-data",
        "title": "SYNTHETIC",
        "origin": "synthetic",
        "manifest": {
            "sources": ["TEST-source"],
            "coverage": {"synthetic": True},
            "integrity": "synthetic",
        },
        "events": events,
    }
    rule = {
        "version": RULE,
        "event_type": "transfer",
        "classification": "confidential",
        "forbidden_destinations": ["forbidden"],
    }
    coverage = {
        s: {"event_ids": sorted(e["id"] for e in events if e["actor"] == s), "complete": True}
        for s in subjects
    }
    manifest = input_manifest(data, rule, coverage)
    expires = time.time() + 3600
    auth = {
        "dataset_ids": ["TEST-data"],
        "subjects": subjects,
        "privacy_domain": "TEST-org",
        "grant_id": "TEST-grant",
        "expires_at": expires,
    }
    permission = {
        "domain": "aiab-confidential/source/v1",
        "plan_digest": plan_digest(plan),
        "scope": plan["scope"],
        "dataset_manifest": manifest,
        "authorization": auth,
        "expires_at": expires,
    }
    entry = {
        "plan": plan,
        "owner": "TEST-owner",
        "dataset_manifest": manifest,
        "authorization": auth,
        "roster_id": "TEST-roster",
        "source_authority": "TEST-authority",
        "source_grant": sign(permission, keys["TEST-authority"]),
    }
    input_key, receipt_key = X25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    from aiab.workbench.model import MODEL_PROFILE

    profile = {
        "domain": PROFILE,
        "assurance": "test-attestation",
        "expires_at": expires,
        "host": "TEST-runtime",
        "repo": "TEST-reviewed-runtime",
        "release_digest": "sha256:" + "a" * 64,
        "gates": {
            k: "SYNTHETIC TEST EVIDENCE ONLY"
            for k in (
                "measured_image",
                "protected_storage",
                "no_ui_plaintext",
                "sandbox_review",
                "collector_review",
                "source_authority_review",
            )
        },
        "model_profile": MODEL_PROFILE,
        "input_public_key": b64(input_key.public_key().public_bytes_raw()),
        "receipt_public_key": b64(receipt_key.public_key().public_bytes_raw()),
        "identities": identities,
        "collectors": ["TEST-collector"],
        "rosters": [
            {
                "roster_id": "TEST-roster",
                "subjects": subjects,
                "actor": "TEST-authority",
                "source_commitment": "TEST-directory",
                "scope_id": "TEST-org",
            }
        ],
        "policies": [
            {
                "domain": "TEST-org",
                "actor": "TEST-authority",
                "max_releases": 1,
                "min_group_size": 2,
                "families": {
                    "TEST-boundary": {
                        "subjects": subjects,
                        "fields": ["counts", "status", "coverage"],
                        "query_digest": query_digest(plan),
                    }
                },
            }
        ],
        "plans": [entry],
    }
    bundle, root = (
        sign(profile, keys["TEST-root"]),
        b64(keys["TEST-root"].public_key().public_bytes_raw()),
    )
    runtime = ConfidentialRuntime(
        bundle=bundle,
        authority_key=root,
        private_dir=tmp_path,
        input_key=input_key,
        result_key=b"r" * 32,
        receipt_key=receipt_key,
        test_attestation=True,
    )
    digest = plan_digest(plan)
    approvals = [
        sign(
            runtime.store.approval_payload(
                digest, who, nonce="TEST-approval-" + who, expires_at=expires
            ),
            keys[who],
        )
        for who in ["TEST-owner", *subjects]
    ]
    return SimpleNamespace(**locals())


@pytest.fixture
def setup(tmp_path):
    return synthetic_fixture(tmp_path)


def collect_args(s, connection):
    return dict(
        bundle=s.bundle,
        authority_key=s.root,
        digest=s.digest,
        approvals=s.approvals,
        dataset=s.data,
        host=s.profile["host"],
        repo=s.profile["repo"],
        release_digest=s.profile["release_digest"],
        principal="TEST-owner",
        principal_key=s.keys["TEST-owner"],
        collector="TEST-collector",
        collector_key=s.keys["TEST-collector"],
        connection=connection,
        test_attestation=True,
    )


async def finish(client, run_id, bearer):
    for _ in range(30):
        response = await client.get(
            "/v1/runs/" + run_id, headers={"authorization": "Bearer " + bearer}
        )
        if response.json()["status"] not in ("queued", "running"):
            return response
        await asyncio.sleep(0.01)
    pytest.fail("run did not finish")


async def test_actual_factory_collector_runner_receipt_and_no_plaintext_routes(setup):
    s = setup
    app = s.runtime.app()
    connection = TestAttestedConnection(app)
    bearer = token("TEST-owner", s.keys["TEST-owner"], s.runtime.profile_digest)
    async with app.router.lifespan_context(app):
        submitted = await collect(**collect_args(s, connection), request_id="TEST-request-0000001")
        assert submitted["assurance"] == "test-attestation"
        # Collector transmits only sealed input on the submission wire.
        wire = next(body for method, path, body in connection.calls if path == "/v1/runs")
        assert b"TEST-PRIVATE-SENTINEL" not in wire and b'"dataset"' not in wire
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://TEST-runtime"
        ) as client:
            status = await finish(client, submitted["run_id"], bearer)
            assert status.json()["status"] == "completed", status.text
            assert "SENTINEL" not in status.text
            for path in (
                "/docs",
                "/openapi.json",
                "/v1/raw",
                "/v1/trace",
                "/v1/runs/" + submitted["run_id"] + "/raw",
            ):
                assert (await client.get(path)).status_code == 404
            headers = {"authorization": "Bearer " + bearer}
            released = await client.post(
                "/v1/runs/" + submitted["run_id"] + "/release", headers=headers
            )
            assert released.status_code == 200, released.text
            package = released.json()["result"]
            assert package["projection"]["counts"]["values"]["supported"] == 2
            assert "TEST-alice" not in released.text and "SENTINEL" not in released.text
            GovernanceStore.verify_receipt(
                package,
                s.profile["receipt_public_key"],
                expected={
                    "plan_digest": s.digest,
                    "binding_digest": submitted["binding_digest"],
                    "recipient": "TEST-owner",
                },
            )
            tampered = deepcopy(package)
            tampered["projection"]["status"] = "tampered"
            with pytest.raises(ValueError):
                GovernanceStore.verify_receipt(tampered, s.profile["receipt_public_key"])
            # Separate recipient can release, but cannot inspect owner's status.
            recipient = {
                "authorization": "Bearer "
                + token("TEST-recipient", s.keys["TEST-recipient"], s.runtime.profile_digest)
            }
            assert (
                await client.get("/v1/runs/" + submitted["run_id"], headers=recipient)
            ).status_code == 404
            assert (
                await client.post("/v1/runs/" + submitted["run_id"] + "/release", headers=recipient)
            ).status_code == 200
            assert (
                await client.post("/v1/runs/" + submitted["run_id"] + "/release", headers=headers)
            ).status_code == 403
            assert (await client.post("/v1/runs", content=wire, headers=headers)).status_code == 409
            assert (
                await client.post(
                    "/v1/runs",
                    json={"request_id": "TEST-plaintext-0001", "plan": s.plan, "dataset": s.data},
                    headers=headers,
                )
            ).status_code == 400
    for path in s.tmp_path.glob("*.sqlite*"):
        assert b"TEST-PRIVATE-SENTINEL" not in path.read_bytes()


async def test_revoke_and_source_revoke_prevent_release(setup):
    s = setup
    app = s.runtime.app()
    async with app.router.lifespan_context(app):
        submitted = await collect(**collect_args(s, TestAttestedConnection(app)))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://TEST-runtime"
        ) as client:
            bearer = token("TEST-owner", s.keys["TEST-owner"], s.runtime.profile_digest)
            await finish(client, submitted["run_id"], bearer)
            document = sign(
                {
                    "domain": "aiab-confidential/revoke/v1",
                    "actor": "TEST-alice",
                    "plan_digest": s.digest,
                    "profile_digest": s.runtime.profile_digest,
                },
                s.keys["TEST-alice"],
            )
            assert (await client.post("/v1/consent", json=document)).status_code == 200
            assert (
                await client.post(
                    "/v1/runs/" + submitted["run_id"] + "/release",
                    headers={"authorization": "Bearer " + bearer},
                )
            ).status_code == 404
            # Source authority revocation is independently authenticated and permanent.
            source = sign(
                {
                    "domain": "aiab-confidential/revoke-source/v1",
                    "actor": "TEST-authority",
                    "plan_digest": s.digest,
                    "profile_digest": s.runtime.profile_digest,
                },
                s.keys["TEST-authority"],
            )
            assert (await client.post("/v1/consent", json=source)).status_code == 200
            assert not s.runtime.store.readiness(s.digest, "TEST-owner")["ready"]


@pytest.mark.parametrize(
    "change", ["missing_consent", "tampered_consent", "input", "pins", "test_live"]
)
async def test_collector_denies_before_transport(setup, change):
    s = setup
    conn = TestAttestedConnection(s.runtime.app())
    args = collect_args(s, conn)
    if change == "missing_consent":
        args["approvals"] = s.approvals[:-1]
    elif change == "tampered_consent":
        args["approvals"] = deepcopy(s.approvals)
        args["approvals"][0]["payload"]["binding_digest"] = "wrong"
    elif change == "input":
        args["dataset"] = deepcopy(s.data)
        args["dataset"]["events"][0]["actor"] = "TEST-bob"
    elif change == "pins":
        args["release_digest"] = "sha256:" + "b" * 64
    else:
        args["test_attestation"] = False
    with pytest.raises((ValueError, InvalidSignature)):
        await collect(**args)
    assert not conn.verified


async def test_wrong_ticket_binding_aad_tamper_and_approval_replay(setup):
    s = setup
    app = s.runtime.app()
    bearer = token("TEST-owner", s.keys["TEST-owner"], s.runtime.profile_digest)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://TEST-runtime"
        ) as client:
            for approval in s.approvals:
                assert (await client.post("/v1/consent", json=approval)).status_code == 200
            assert (await client.post("/v1/consent", json=s.approvals[0])).status_code == 403
            headers = {"authorization": "Bearer " + bearer}
            ticket = sign(
                {
                    "domain": SUBMISSION,
                    "collector": "TEST-collector",
                    "principal": "TEST-owner",
                    "request_id": "TEST-ticket-0000001",
                    "plan_digest": s.digest,
                    "input_digest": dataset_digest(s.data),
                    "profile_digest": "WRONG",
                    "input_public_key": s.profile["input_public_key"],
                    "assurance": "test-attestation",
                    "expires_at": time.time() + 300,
                },
                s.keys["TEST-collector"],
            )
            payload = {"plan": s.plan, "dataset": s.data, "collector": ticket}
            sealed = seal(
                s.profile["input_public_key"], payload, "TEST-owner", "TEST-ticket-0000001"
            )
            response = await client.post(
                "/v1/runs",
                json={"request_id": "TEST-ticket-0000001", "sealed_input": sealed},
                headers=headers,
            )
            assert response.status_code == 400
            # Wrong AAD and authenticated ciphertext modification fail before admission.
            for ciphertext in [
                sealed,
                {**sealed, "ciphertext": sealed["ciphertext"][:-4] + "AAAA"},
            ]:
                response = await client.post(
                    "/v1/runs",
                    json={"request_id": "TEST-other-0000001", "sealed_input": ciphertext},
                    headers=headers,
                )
                assert response.status_code == 400


def test_independent_aggregate_rejects_same_cardinality_wrong_attribution(setup):
    s = setup
    for approval in s.approvals:
        p = approval["payload"]
        s.runtime.store.approve(
            s.digest,
            p["actor"],
            signature=approval["signature"],
            nonce=p["nonce"],
            expires_at=p["expires_at"],
        )
    who = s.runtime.authenticate(
        token("TEST-owner", s.keys["TEST-owner"], s.runtime.profile_digest)
    )
    request_id = "TEST-direct-0000001"
    ticket = sign(
        {
            "domain": SUBMISSION,
            "collector": "TEST-collector",
            "principal": who.id,
            "request_id": request_id,
            "plan_digest": s.digest,
            "input_digest": dataset_digest(s.data),
            "profile_digest": s.runtime.profile_digest,
            "input_public_key": s.profile["input_public_key"],
            "assurance": "test-attestation",
            "expires_at": time.time() + 300,
        },
        s.keys["TEST-collector"],
    )
    actor = s.runtime.admit(
        who, s.plan, s.data, submission={"collector": ticket}, request_id=request_id
    )
    result = subject_result(s.plan, s.data, s.manifest)
    result["findings"][1]["subject"] = result["findings"][0]["subject"]
    with pytest.raises(ValueError, match="attribution"):
        s.runtime.release(who, actor["governance_run_id"], result)
    result = subject_result(s.plan, s.data, s.manifest)
    result["findings"][0]["evidence_ids"] = ["WRONG"]
    with pytest.raises(ValueError, match="attribution"):
        s.runtime.release(who, actor["governance_run_id"], result)


def test_production_rejects_unsigned_stale_absent_gates_and_test_profile(setup, tmp_path):
    s = setup
    config = dict(
        bundle=s.bundle,
        authority_key=s.root,
        private_dir=tmp_path / "other",
        input_key=s.input_key,
        result_key=b"r" * 32,
        receipt_key=s.receipt_key,
    )
    with pytest.raises(ValueError, match="test/live"):
        ConfidentialRuntime(**config)
    for change in ("stale", "gate", "key"):
        profile = deepcopy(s.profile)
        if change == "stale":
            profile["expires_at"] = time.time() - 1
        elif change == "gate":
            profile["gates"].pop("protected_storage")
        else:
            profile["input_public_key"] = b64(
                X25519PrivateKey.generate().public_key().public_bytes_raw()
            )
        config["bundle"] = sign(profile, s.keys["TEST-root"])
        with pytest.raises(ValueError):
            ConfidentialRuntime(**config, test_attestation=True)
    config["bundle"]["payload"]["expires_at"] = time.time() + 300
    with pytest.raises(InvalidSignature):
        ConfidentialRuntime(**config, test_attestation=True)


def test_missing_coverage_and_small_cells_are_not_negative_or_zero(setup):
    s = setup
    data, manifest = deepcopy(s.data), deepcopy(s.manifest)
    data["events"][0]["attributes"]["destination"] = "allowed"
    manifest["subject_coverage"]["TEST-alice"]["complete"] = False
    result = subject_result(s.plan, data, manifest)
    assert [f["outcome"] for f in result["findings"]] == ["insufficient_evidence", "supported"]
    # Projection suppresses the entire counts object, not an exposing complement.
    with s.runtime.store._db() as db:
        record = s.runtime.store._get(db, "plan", s.digest)
        policy = s.runtime.store._get(db, "policy", "TEST-org")
    projection = s.runtime.store._project(record, result, "TEST-owner", policy)
    assert projection["counts"] == {"state": "suppressed", "value": None}


async def test_transport_verification_failure_sends_nothing(setup):
    s = setup

    class FailedAttestation(TestAttestedConnection):
        async def start(self):
            raise ValueError("TEST MR_SEAM failure")

        async def close(self):
            pass

    conn = FailedAttestation(s.runtime.app())
    with pytest.raises(ValueError, match="MR_SEAM"):
        await collect(**collect_args(s, conn))
    assert conn.calls == []


async def test_cancel_completed_run_revokes_release_and_persists(setup):
    s = setup
    app = s.runtime.app()
    bearer = token("TEST-owner", s.keys["TEST-owner"], s.runtime.profile_digest)
    async with app.router.lifespan_context(app):
        admitted = await collect(**collect_args(s, TestAttestedConnection(app)))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://TEST-runtime"
        ) as client:
            await finish(client, admitted["run_id"], bearer)
            headers = {"authorization": "Bearer " + bearer}
            response = await client.post(
                "/v1/runs/" + admitted["run_id"] + "/cancel", headers=headers
            )
            assert response.json()["status"] == "cancelled"
            assert (
                await client.post("/v1/runs/" + admitted["run_id"] + "/release", headers=headers)
            ).status_code == 404
    restored = ConfidentialRuntime(
        bundle=s.bundle,
        authority_key=s.root,
        private_dir=s.tmp_path,
        input_key=s.input_key,
        result_key=b"r" * 32,
        receipt_key=s.receipt_key,
        test_attestation=True,
    )
    with pytest.raises(ValueError, match="blocked"):
        restored.revalidate(admitted["governance_run_id"])


def test_same_root_cannot_replace_immutable_source_population(setup):
    s = setup
    profile = deepcopy(s.profile)
    profile["collectors"] = []
    bundle = sign(profile, s.keys["TEST-root"])
    with pytest.raises(ValueError, match="immutable"):
        ConfidentialRuntime(
            bundle=bundle,
            authority_key=s.root,
            private_dir=s.tmp_path,
            input_key=s.input_key,
            result_key=b"r" * 32,
            receipt_key=s.receipt_key,
            test_attestation=True,
        )
