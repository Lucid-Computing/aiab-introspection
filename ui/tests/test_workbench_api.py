import base64
import copy

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
    plan = service.catalog()[0]["plan"]
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
