import asyncio
import base64
import copy
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.workbench_api import WorkbenchAPI


@pytest.fixture
def api(tmp_path):
    service = WorkbenchAPI(
        tmp_path,
        lambda r: {"username": r.headers["test-user"]} if "test-user" in r.headers else None,
    )
    app = FastAPI()
    app.include_router(service.router)
    with TestClient(app) as client:
        client.headers.update({"test-user": "alice", "origin": "http://testserver"})
        yield service, client


def test_rehearsal_source_drilldown_is_owner_scoped_and_reference_bound(api):
    service, client = api
    plan = service.catalog()[0]["plan"]
    event = service.dataset("alice", "northstar")["events"][0]
    run = service.store.enqueue("alice", plan, "digest", "northstar", "rehearse")
    assert service.store.claim("alice", run)
    service.store.finish("alice", run, result={"findings": [{"evidence_ids": [event["id"]]}]})
    endpoint = f"/api/workbench/runs/{run}/evidence/{event['id']}"
    response = client.get(endpoint)
    assert response.status_code == 200
    assert response.json()["event"]["id"] == event["id"]
    assert "native_bytes" not in json.dumps(response.json())
    assert client.get(endpoint, headers={"test-user": "bob"}).status_code == 404
    assert client.get(f"/api/workbench/runs/{run}/evidence/not-cited").status_code == 404
    audit = service.store.enqueue("alice", plan, "digest", "northstar", "audit")
    assert client.get(f"/api/workbench/runs/{audit}/evidence/{event['id']}").status_code == 403


def test_hosted_origin_is_configured_not_forwarded_by_caller(api, monkeypatch):
    service, client = api
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://astra.example")
    key = Ed25519PrivateKey.generate()
    body = {"public_key": base64.b64encode(key.public_key().public_bytes_raw()).decode()}
    endpoint = "/api/workbench/identities/challenge"
    assert (
        client.post(endpoint, json=body, headers={"origin": "https://astra.example"}).status_code
        == 200
    )
    assert (
        client.post(
            endpoint,
            json=body,
            headers={
                "origin": "https://evil.example",
                "x-forwarded-host": "evil.example",
                "x-forwarded-proto": "https",
            },
        ).status_code
        == 403
    )


def test_auth_origin_and_plan_isolation(api):
    service, client = api
    assert client.get("/api/workbench/bootstrap").json()["user"]["id"] == "alice"
    plan = service.catalog()[0]["plan"]
    response = client.post("/api/workbench/plans", json={"plan": plan})
    assert response.status_code == 200
    digest = response.json()["digest"]
    url = "/api/workbench/plans/" + plan["id"]
    assert client.get(url, headers={"test-user": "bob"}).status_code == 404
    assert client.put(url, json={"plan": plan, "expected_digest": "stale"}).status_code == 409
    assert (
        client.put(
            url,
            json={"plan": plan, "expected_digest": digest},
            headers={"origin": "https://evil.example"},
        ).status_code
        == 403
    )
    del client.headers["test-user"]
    assert client.get(url).status_code == 401


def test_real_key_enrollment_signature_replay_and_immutable_identity(api):
    service, client = api
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    challenge = client.post(
        "/api/workbench/identities/challenge", json={"public_key": public}
    ).json()
    payload = base64.b64decode(challenge["payload"])
    body = {
        "challenge_id": challenge["challenge_id"],
        "signature": base64.b64encode(key.sign(payload)).decode(),
    }
    assert client.post("/api/workbench/identities/enroll", json=body).status_code == 200
    assert client.post("/api/workbench/identities/enroll", json=body).status_code == 400
    with pytest.raises(ValueError, match="immutable"):
        other = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
        service.governance.enroll_identity("alice", base64.b64encode(other).decode())


def test_catalog_scope_checks_and_import_cannot_bypass_consent(api):
    service, client = api
    plan = executable_plan(service)
    assert client.post("/api/workbench/check", json={"plan": plan}).json()["valid"]
    client.post("/api/workbench/plans", json={"plan": plan})
    assert (
        client.post(
            "/api/workbench/runs",
            json={"plan_id": plan["id"], "dataset_id": "northstar", "mode": "audit"},
        ).status_code
        == 403
    )
    dataset = service.dataset("alice", "northstar")
    dataset["id"] = "untrusted-import"
    service.store.add_dataset("alice", dataset)
    changed = copy.deepcopy(plan)
    changed["id"] = "untrusted-plan"
    changed["scope"]["dataset_ids"] = [dataset["id"]]
    client.post("/api/workbench/plans", json={"plan": changed})
    result = client.post(
        "/api/workbench/runs",
        json={"plan_id": changed["id"], "dataset_id": dataset["id"], "mode": "rehearse"},
    )
    assert result.status_code == 403
    assert result.json()["error"]["code"] == "consent_required"


def executable_plan(service):
    """Exact valid runtime config; production catalog integration is parent-owned."""
    from aiab.workbench.model import MODEL_PROFILE

    plan = copy.deepcopy(service.catalog()[0]["plan"])
    plan["mode"] = "joint"
    for step in plan["steps"]:
        if step["kind"] == "agent":
            step["config"] = {
                "instructions": "Investigate agreed scope",
                "model_profile": MODEL_PROFILE,
            }
    plan["agent"]["model_profile"] = MODEL_PROFILE
    return plan


def enroll_browser(client, actor, key):
    headers = {"test-user": actor}
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    item = client.post(
        "/api/workbench/identities/challenge", headers=headers, json={"public_key": public}
    ).json()
    response = client.post(
        "/api/workbench/identities/enroll",
        headers=headers,
        json={
            "challenge_id": item["challenge_id"],
            "signature": base64.b64encode(key.sign(base64.b64decode(item["payload"]))).decode(),
        },
    )
    assert response.status_code == 200, response.text


def signed_approval(client, plan, digest, actor, key, nonce):
    url = "/api/workbench/plans/" + plan["id"]
    body = {"digest": digest, "nonce": nonce}
    response = client.post(url + "/approval-payload", headers={"test-user": actor}, json=body)
    assert response.status_code == 200, response.text
    payload = base64.b64decode(response.json()["payload"])
    body["signature"] = base64.b64encode(key.sign(payload)).decode()
    return client.post(url + "/approve", headers={"test-user": actor}, json=body), body


@pytest.fixture
def joint(api, monkeypatch):
    from aiab.workbench import contracts, runner

    from server.workbench_admin import TRUST, provision

    service, client = api
    plan = executable_plan(service)
    plan["scope"]["subjects"] = ["alice", "bob"]
    plan["approval"]["required_signers"] = ["signer"]
    plan["disclosure"]["query_family"] = "family"
    plan["disclosure"]["min_group_size"] = 1
    plan["disclosure"]["recipients"] = [
        {
            "id": actor,
            "role": "reviewer" if actor == "reviewer" else "reader",
            "fields": ["status", "findings"],
            "allow_evidence": False,
            "allow_excerpts": False,
        }
        for actor in ["alice", "reader", "reviewer"]
    ]
    keys = {
        actor: Ed25519PrivateKey.generate()
        for actor in ["alice", "bob", "signer", "reader", "reviewer", "authority"]
    }
    for actor, key in keys.items():
        if actor in {"authority", "reviewer"}:
            provision(
                service.directory,
                "identity",
                {
                    "trust": TRUST,
                    "actor": actor,
                    "public_key": base64.b64encode(key.public_key().public_bytes_raw()).decode(),
                    "roles": ["challenge_reviewer"]
                    if actor == "reviewer"
                    else ["source_authority", "release_authority", "roster_authority"],
                },
            )
        else:
            enroll_browser(client, actor, key)
    result = client.post("/api/workbench/plans", json={"plan": plan})
    assert result.status_code == 200, result.text
    digest = result.json()["digest"]
    provision(
        service.directory,
        "releasepolicy",
        {
            "trust": TRUST,
            "authority": "authority",
            "domain": "demo",
            "max_releases": 3,
            "min_group_size": 1,
            "families": {"family": {"plan": plan, "fields": ["status", "findings"]}},
        },
    )
    data = service.dataset("alice", "northstar")
    manifest = {"dataset_digest": contracts.dataset_digest(data), "manifest": data["manifest"]}
    provision(
        service.directory,
        "sourcegrant",
        {
            "trust": TRUST,
            "authority": "authority",
            "owner": "alice",
            "plan": plan,
            "source_commitment": "test-operator-reviewed-full-manifest",
            "dataset_manifest": manifest,
            "authorization": {
                "dataset_ids": ["northstar"],
                "subjects": ["alice", "bob"],
                "grant_id": "demo-grant",
                "privacy_domain": "demo",
                "expires_at": None,
            },
        },
    )
    assert client.post(f"/api/workbench/plans/{plan['id']}/bind").status_code == 200
    private_result = {
        "status": "completed",
        "trace": [{"secret": "PRIVATE_TRACE"}],
        "usage": {"secret": "PRIVATE_USAGE"},
        "raw": "PRIVATE_INPUT",
        "findings": [
            {
                "id": "private-finding",
                "outcome": "supported",
                "title": "Claim",
                "claim": "Permitted claim",
                "evidence_ids": ["PRIVATE_EVIDENCE"],
                "details": {"secret": "PRIVATE_DETAIL"},
            }
        ],
    }
    calls = []

    async def execute(plan, data, *, actor, on_event):
        calls.append(actor)
        await on_event({"private": "PRIVATE_EVENT"})
        return copy.deepcopy(private_result)

    monkeypatch.setattr(runner, "execute_plan", execute)
    return service, client, plan, digest, keys, private_result, calls


def approve_joint(joint):
    _, client, plan, digest, keys, *_ = joint
    for actor in ["alice", "bob", "signer"]:
        response, _ = signed_approval(client, plan, digest, actor, keys[actor], actor + "-nonce")
        assert response.status_code == 200, response.text


async def drain_runs(service):
    await asyncio.gather(*list(service.tasks.values()), return_exceptions=True)


def start_joint(joint, request_id="request-1"):
    service, client, plan, *_ = joint
    response = client.post(
        "/api/workbench/runs",
        json={
            "plan_id": plan["id"],
            "dataset_id": "northstar",
            "mode": "audit",
            "request_id": request_id,
        },
    )
    if response.status_code == 200:
        client.portal.call(drain_runs, service)
    return response


def test_joint_discovery_consent_release_and_independent_receipt(joint):
    from aiab.workbench.governance import GovernanceStore

    service, client, plan, digest, keys, original, calls = joint
    for actor in ["bob", "reader", "reviewer", "signer"]:
        items = client.get("/api/workbench/plans", headers={"test-user": actor}).json()["plans"]
        assert [p["digest"] for p in items] == [digest]
        boot = client.get("/api/workbench/bootstrap", headers={"test-user": actor}).json()
        assert boot["plans"] == items
        assert len(boot["datasets"]) == 1  # no owner's imported data
    assert (
        client.get("/api/workbench/plans", headers={"test-user": "outsider"}).json()["plans"] == []
    )
    assert start_joint(joint).status_code == 400
    approve_joint(joint)
    assert client.get(f"/api/workbench/plans/{plan['id']}/readiness").json() == {"ready": True}
    response = start_joint(joint)
    assert response.status_code == 200, response.text
    ident = response.json()["run_id"]
    client.get(f"/api/workbench/runs/{ident}")
    assert calls == [
        {
            "id": "alice",
            "authorized_dataset_ids": ["northstar"],
            "authorized_subjects": ["alice", "bob"],
        }
    ]
    for actor in ["bob", "signer", "outsider"]:
        assert (
            client.get(f"/api/workbench/runs/{ident}", headers={"test-user": actor}).status_code
            == 404
        )
    url = f"/api/workbench/runs/{ident}"
    released = client.get(url, headers={"test-user": "reader"})
    assert released.status_code == 200, released.text
    assert "PRIVATE" not in released.text and "private-finding" not in released.text
    assert client.get(url, headers={"test-user": "reader"}).json() == released.json()
    package = {"projection": released.json()["result"], "receipt": released.json()["receipt"]}
    key = client.get("/api/workbench/receipts/public-key").json()["public_key"]
    assert GovernanceStore.verify_receipt(
        package, key, expected={"recipient": "reader", "plan_digest": digest}
    )
    assert client.post(
        "/api/workbench/receipts/verify", json={"package": package, "public_key": key}
    ).json()["verified"]
    bad = copy.deepcopy(package)
    bad["projection"]["status"] = "forged"
    assert (
        client.post(
            "/api/workbench/receipts/verify", json={"package": bad, "public_key": key}
        ).status_code
        == 400
    )
    assert service.store.job("alice", ident)["result"] == original
    assert client.post(url + "/cancel", headers={"test-user": "reader"}).status_code == 404
    assert start_joint(joint).status_code == 400  # admission request replay


def test_replay_edit_invalidates_pending_signatures_and_old_release(joint):
    service, client, plan, digest, keys, *_ = joint
    approve_joint(joint)
    response, approval = signed_approval(client, plan, digest, "alice", keys["alice"], "new-nonce")
    assert response.status_code == 200
    assert (
        client.post(f"/api/workbench/plans/{plan['id']}/approve", json=approval).status_code == 400
    )
    ident = start_joint(joint).json()["run_id"]
    assert (
        client.get(f"/api/workbench/runs/{ident}", headers={"test-user": "reader"}).status_code
        == 200
    )
    changed = copy.deepcopy(plan)
    changed["question"] += " Changed"
    assert (
        client.put(
            f"/api/workbench/plans/{plan['id']}", json={"plan": changed, "expected_digest": digest}
        ).status_code
        == 200
    )
    assert (
        client.post(f"/api/workbench/plans/{plan['id']}/approve", json=approval).status_code == 409
    )
    assert client.get(f"/api/workbench/runs/{ident}").status_code == 400
    assert (
        client.get(f"/api/workbench/runs/{ident}", headers={"test-user": "reader"}).status_code
        == 404
    )
    assert (
        client.put(
            f"/api/workbench/plans/{plan['id']}",
            json={
                "plan": plan,
                "expected_digest": service.store.plan("alice", plan["id"])["digest"],
            },
        ).status_code
        == 400
    )


def test_review_notes_exact_versions_and_authorization(joint):
    from aiab.workbench.governance import commitment

    from server.workbench_store import WorkbenchStore

    service, client, plan, digest, *_ = joint
    url = f"/api/workbench/plans/{plan['id']}/reviews"
    body = {
        "digest": digest,
        "clause": "/criteria/0/description",
        "note": "Clarify policy version",
        "recipients": ["alice", "bob"],
    }
    response = client.post(url, headers={"test-user": "bob"}, json=body)
    assert response.status_code == 200, response.text
    note = response.json()
    assert note["clause_digest"] == commitment(
        "aiab-workbench/clause/v1", plan["criteria"][0]["description"]
    )
    assert WorkbenchStore(service.store.path).annotations("review", digest, "bob") == [note]
    assert client.get(url, headers={"test-user": "reader"}).json() == {"reviews": []}
    assert client.post(url, headers={"test-user": "outsider"}, json=body).status_code == 404
    assert client.post(url, json=dict(body, recipients=["outsider"])).status_code == 400
    assert client.post(url, json=dict(body, digest="stale")).status_code == 409
    assert client.post(url, json=dict(body, clause="/criteria/999")).status_code == 400
    assert not service.governance.readiness(digest, "alice")["ready"]


def test_challenge_independent_disposition_and_preserved_original(joint):
    service, client, plan, digest, keys, original, _ = joint
    approve_joint(joint)
    ident = start_joint(joint).json()["run_id"]
    body = {
        "finding_id": "finding-1",
        "correction": "Authenticated external review exists",
        "evidence_ids": [],
        "requested_remedy": "Reconsider finding",
        "recipients": ["reader", "reviewer", "alice"],
    }
    url = f"/api/workbench/runs/{ident}/challenge"
    response = client.post(url, headers={"test-user": "reader"}, json=body)
    assert response.status_code == 200, response.text
    first = response.json()
    child = dict(
        body, parent_id=first["id"], disposition="upheld", recipients=["reader", "reviewer"]
    )
    assert client.post(url, json=child).status_code == 400  # owner conflict
    assert client.post(url, headers={"test-user": "reader"}, json=child).status_code == 400
    reviewed = client.post(url, headers={"test-user": "reviewer"}, json=child)
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["parent_id"] == first["id"]
    assert (
        client.post(
            url, headers={"test-user": "reviewer"}, json=dict(child, recipients=["bob"])
        ).status_code
        == 400
    )
    assert (
        client.post(
            url, headers={"test-user": "reader"}, json=dict(body, evidence_ids=["PRIVATE_EVIDENCE"])
        ).status_code
        == 400
    )
    assert client.get(
        f"/api/workbench/runs/{ident}/challenges", headers={"test-user": "bob"}
    ).json() == {"challenges": []}
    rows = client.get(
        f"/api/workbench/runs/{ident}/challenges", headers={"test-user": "reader"}
    ).json()["challenges"]
    assert len(rows) == 2 and all("evidence_ids" not in r for r in rows)
    assert service.store.job("alice", ident)["result"] == original
    assert "challenge" not in str(client.get("/api/workbench/history").json())

    # Withdrawal blocks new releases, but must not remove the right to challenge
    # a finding already disclosed to this principal.
    assert (
        client.post(
            f"/api/workbench/plans/{plan['id']}/revoke",
            headers={"test-user": "bob"},
            json={"digest": digest},
        ).status_code
        == 200
    )
    assert client.post(url, headers={"test-user": "reader"}, json=body).status_code == 200


def test_source_removal_revocation_cancellation_and_recipient_denials(joint):
    import json

    service, client, plan, digest, keys, *_ = joint
    approve_joint(joint)
    ident = start_joint(joint).json()["run_id"]
    path = service.directory / "source-grants.json"
    grants = path.read_text()
    path.write_text(json.dumps([]))
    assert client.get(f"/api/workbench/runs/{ident}").status_code == 403
    path.write_text(grants)
    assert (
        client.post(
            f"/api/workbench/plans/{plan['id']}/revoke",
            headers={"test-user": "bob"},
            json={"digest": digest},
        ).status_code
        == 200
    )
    denied = client.get(f"/api/workbench/runs/{ident}")
    assert denied.status_code == 400 and "bob" not in denied.text
    response, _ = signed_approval(client, plan, digest, "bob", keys["bob"], "again")
    assert response.status_code == 200
    assert client.get(f"/api/workbench/runs/{ident}").status_code == 400  # permanently blocked
    assert client.post(f"/api/workbench/runs/{ident}/cancel").status_code == 200
    assert service.store.job("alice", ident)["result"] is not None  # original preserved


def test_synthetic_challenges_are_durable_owner_only_without_receipts(api, monkeypatch):
    from aiab.workbench import runner

    from server.workbench_store import WorkbenchStore

    service, client = api
    plan = executable_plan(service)
    client.post("/api/workbench/plans", json={"plan": plan})
    calls = []

    async def execute(plan, data, *, actor, on_event):
        calls.append(actor)
        return {"findings": [{"id": "f1"}], "trace": ["synthetic"]}

    monkeypatch.setattr(runner, "execute_plan", execute)
    response = client.post(
        "/api/workbench/runs",
        json={"plan_id": plan["id"], "dataset_id": "northstar", "mode": "rehearse"},
    )
    assert response.status_code == 200, response.text
    ident = response.json()["run_id"]
    client.portal.call(drain_runs, service)
    assert calls[0]["authorized_dataset_ids"] == ["northstar"]
    assert calls[0]["authorized_subjects"] == sorted(
        {e["actor"] for e in service.dataset("alice", "northstar")["events"] if e["actor"]}
    )
    body = {
        "finding_id": "f1",
        "correction": "Synthetic correction",
        "evidence_ids": [],
        "requested_remedy": "Review",
        "recipients": ["alice"],
    }
    url = f"/api/workbench/runs/{ident}"
    result = client.post(url + "/challenge", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["verification"] == "synthetic_unverified"
    assert "receipt" not in result.json()
    assert WorkbenchStore(service.store.path).annotations("challenge", ident, "alice") == [
        result.json()
    ]
    assert (
        client.post(url + "/challenge", json=dict(body, recipients=["alice", "bob"])).status_code
        == 400
    )
    assert client.get(url + "/challenges", headers={"test-user": "bob"}).status_code == 404
    assert client.post(url + "/release").status_code != 200


def test_source_grant_checked_in_private_runtime_callback(joint, monkeypatch):
    from aiab.workbench import runner

    service, client, _, _, _, _, _ = joint
    approve_joint(joint)

    async def revoked_during_execution(plan, dataset, *, actor, on_event):
        (service.directory / "source-grants.json").write_text("[]")
        await on_event({"private": "must never be published"})
        pytest.fail("Revoked source authorization must stop execution")

    monkeypatch.setattr(runner, "execute_plan", revoked_during_execution)
    ident = start_joint(joint).json()["run_id"]
    job = service.store.job("alice", ident)
    assert job["status"] == "failed" and job["result"] is None
    assert "must never" not in client.get(f"/api/workbench/runs/{ident}").text


def test_pending_run_cancellation_wins_over_completion(joint, monkeypatch):
    from aiab.workbench import runner

    service, client, plan, *_ = joint
    approve_joint(joint)

    async def blocked(plan, dataset, *, actor, on_event):
        await on_event({"private": True})
        await asyncio.Future()

    monkeypatch.setattr(runner, "execute_plan", blocked)
    response = client.post(
        "/api/workbench/runs",
        json={
            "plan_id": plan["id"],
            "dataset_id": "northstar",
            "mode": "audit",
            "request_id": "pending",
        },
    )
    ident = response.json()["run_id"]
    assert (
        client.post(f"/api/workbench/runs/{ident}/cancel", headers={"test-user": "bob"}).status_code
        == 404
    )
    assert client.post(f"/api/workbench/runs/{ident}/cancel").json()["status"] == "cancelled"
    client.portal.call(drain_runs, service)
    assert not service.store.finish("alice", ident, result={"secret": "late"})
    assert service.store.job("alice", ident)["result"] is None
    assert (
        client.post(
            f"/api/workbench/runs/{ident}/release", headers={"test-user": "reader"}
        ).status_code
        == 400
    )


def test_confidential_admission_never_accepts_fabricated_runtimefacts(api):
    service, client = api
    plan = executable_plan(service)
    plan["mode"] = "confidential"
    plan["approval"]["roster"] = plan["scope"]["subjects"]
    response = client.post("/api/workbench/plans", json={"plan": plan})
    assert response.status_code == 200, response.text
    response = client.post(
        "/api/workbench/runs",
        json={
            "plan_id": plan["id"],
            "dataset_id": "northstar",
            "mode": "audit",
            "runtime_facts": {"attestation_verified": True},
            "authorization": {"all": True},
            "actor": {"id": "authority"},
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "sealed_runtime_required"
    assert not service.tasks
