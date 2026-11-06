"""Independent authorization probes preserving the secure behavior for reported findings.

No provider requests, inference or hosted writes. All storage is tmp_path.
Shared fixtures provision synthetic authority in a disposable local FastAPI app.
"""

import copy
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_workbench_api import api as api_fixture
from test_workbench_api import approve_joint, start_joint
from test_workbench_api import joint as joint_fixture

from server import auth
from server.workbench_api import WorkbenchAPI

api = api_fixture
joint = joint_fixture


@pytest.fixture
def session_app(tmp_path, monkeypatch):
    from server import app as web

    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.setattr(web, "oidc", None)
    monkeypatch.setattr(web, "signer", auth.SessionSigner(b"synthetic-tester-session-key"))
    monkeypatch.setattr(
        web, "settings", replace(web.settings, accounts_file=tmp_path / "accounts.json")
    )
    account = auth.Account(
        "tester-local", "auditor", "Synthetic tester", auth.hash_password("synthetic-password")
    )
    auth.save_account(web.settings.accounts_file, account)
    app = FastAPI()
    # Mount production endpoints without production lifespan/background jobs.
    app.add_api_route("/login", web.login, methods=["POST"])
    app.add_api_route("/logout", web.logout, methods=["POST"])
    service = WorkbenchAPI(tmp_path / "workbench", web.user_of)
    app.include_router(service.router)
    with TestClient(app, follow_redirects=False, base_url="https://testserver") as client:
        yield web, service, client


@pytest.mark.parametrize("other_kind", ["oidc-other-domain", "local-password"])
def test_independent_principals_cannot_read_each_others_private_plan(
    session_app, tmp_path, other_kind
):
    web, service, client = session_app
    oidc = auth.OidcLogin(
        "https://issuer.test", "synthetic-client", "unused", admins_file=tmp_path / "admins.json"
    )
    owner = oidc.account({"sub": "subject-owner", "email": "tester-local@one.example"})
    other = (
        oidc.account({"sub": "subject-other", "email": "tester-local@two.example"})
        if other_kind == "oidc-other-domain"
        else auth.authenticate(web.settings.accounts_file, "tester-local", "synthetic-password")
    )
    client.cookies.set(web.COOKIE, web.signer.issue(owner))
    plan = copy.deepcopy(service.catalog()[0]["plan"])
    plan["id"] = "tester-private-" + uuid4().hex
    endpoint = "/api/workbench/plans/" + plan["id"]
    assert (
        client.post(
            "/api/workbench/plans", json={"plan": plan}, headers={"origin": "https://testserver"}
        ).status_code
        == 200
    )
    client.cookies.clear()
    client.cookies.set(web.COOKIE, web.signer.issue(other))
    response = client.get(endpoint)
    assert response.status_code == 404, (
        f"Independent {other_kind} reads private owner plan: HTTP {response.status_code}"
    )


@pytest.mark.parametrize("origin", [None, "https://attacker.example"])
def test_login_rejects_cross_site_session_replacement(session_app, origin):
    web, _, client = session_app
    client.cookies.set(
        web.COOKIE, web.signer.issue(auth.Account("original-victim", "auditor", "Victim", ""))
    )
    headers = {} if origin is None else {"origin": origin}
    response = client.post(
        "/login",
        data={"username": "tester-local", "password": "synthetic-password", "next": "/workbench"},
        headers=headers,
    )
    assert response.status_code == 403, (
        f"Cross-site login replaced browser identity: HTTP {response.status_code}"
    )
    assert not response.cookies


def test_https_session_cookie_is_secure(session_app):
    _, _, client = session_app
    response = client.post(
        "/login",
        data={"username": "tester-local", "password": "synthetic-password"},
        headers={"origin": "https://testserver"},
    )
    assert response.status_code == 303
    assert all("secure" in h.lower().split("; ") for h in response.headers.get_list("set-cookie"))


@pytest.mark.parametrize(
    "origin", [None, "null", "https://attacker.example", "http://testserver/", "https://testserver"]
)
def test_workbench_mutations_reject_nonexact_origins(api, monkeypatch, origin):
    _, client = api
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    client.headers.pop("origin")
    response = client.post(
        "/api/workbench/check", json={}, headers={} if origin is None else {"origin": origin}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_denied"


def test_repeated_release_is_immutable_and_does_not_enable_second_admission(joint):
    service, client, _, _, _, original, _ = joint
    approve_joint(joint)
    run_id = start_joint(joint, "tester-reservation-" + uuid4().hex).json()["run_id"]
    url = f"/api/workbench/runs/{run_id}"
    headers = {"test-user": "reader"}
    first = client.post(url + "/release", headers=headers)
    assert first.status_code == 200
    for _ in range(3):
        assert client.post(url + "/release", headers=headers).json() == first.json()
    assert (
        len(
            service.governance.run_status(
                service.store.job("alice", run_id)["governance_id"], "reader"
            )["releases"]
        )
        == 1
    )
    assert "PRIVATE" not in first.text
    assert service.store.job("alice", run_id)["result"] == original
    assert start_joint(joint, "tester-second-" + uuid4().hex).status_code == 400
    assert client.post(url + "/release", headers={"test-user": "outsider"}).status_code == 404


def test_revocation_blocks_execution_authorize_callback_and_new_release(joint, monkeypatch):
    from aiab.workbench import runner

    service, client, plan, digest, *_ = joint
    approve_joint(joint)
    reached = []

    async def execute(plan, data, *, actor, on_event, authorize):
        assert await authorize() is True
        reached.append("authorized")
        service.governance.revoke(digest, "bob")
        await authorize()
        reached.append("unauthorized-work")
        return {"status": "completed", "findings": []}

    monkeypatch.setattr(runner, "execute_plan", execute)
    run_id = start_joint(joint, "tester-revoke-" + uuid4().hex).json()["run_id"]
    assert reached == ["authorized"]
    job = service.store.job("alice", run_id)
    assert job["status"] == "failed" and job["result"] is None
    assert (
        client.post(
            f"/api/workbench/runs/{run_id}/release", headers={"test-user": "reader"}
        ).status_code
        == 400
    )


def test_receipt_binding_and_challenge_parent_cannot_be_substituted(joint):
    from aiab.workbench.governance import GovernanceStore

    _, client, _, digest, *_ = joint
    approve_joint(joint)
    run_id = start_joint(joint, "tester-receipt-" + uuid4().hex).json()["run_id"]
    url = f"/api/workbench/runs/{run_id}"
    package = client.post(url + "/release", headers={"test-user": "reader"}).json()
    key = client.get("/api/workbench/receipts/public-key").json()["public_key"]
    assert GovernanceStore.verify_receipt(
        package, key, expected={"recipient": "reader", "plan_digest": digest}
    )
    for expected in [{"recipient": "outsider"}, {"plan_digest": "stale"}, {"run_id": "unrelated"}]:
        with pytest.raises(ValueError, match="binding mismatch"):
            GovernanceStore.verify_receipt(package, key, expected=expected)
    forged = copy.deepcopy(package)
    forged["receipt"]["payload"]["recipient"] = "outsider"
    with pytest.raises(ValueError):
        GovernanceStore.verify_receipt(forged, key)
    body = {
        "finding_id": "finding-1",
        "correction": "Synthetic objection",
        "evidence_ids": [],
        "requested_remedy": "Review",
        "recipients": ["reader", "reviewer"],
    }
    first = client.post(url + "/challenge", headers={"test-user": "reader"}, json=body)
    assert first.status_code == 200
    assert (
        client.post(
            url + "/challenge",
            headers={"test-user": "reviewer"},
            json=dict(body, parent_id="nonexistent", disposition="upheld"),
        ).status_code
        == 400
    )
    assert (
        client.post(
            url + "/challenge",
            headers={"test-user": "reviewer"},
            json=dict(
                body,
                parent_id=first.json()["id"],
                disposition="upheld",
                recipients=["reader", "reviewer", "alice"],
            ),
        ).status_code
        == 400
    )


def test_reader_copy_cannot_hide_owner_agreement_from_other_participants(joint):
    _, client, plan, _, *_ = joint
    endpoint = "/api/workbench/plans/" + plan["id"]
    assert client.get(endpoint, headers={"test-user": "bob"}).status_code == 200
    response = client.post(
        "/api/workbench/plans", headers={"test-user": "reader"}, json={"plan": plan}
    )
    assert response.status_code == 200
    response = client.get(endpoint, headers={"test-user": "bob"})
    assert response.status_code == 200, (
        f"Reader's private copy hid owner's agreement: HTTP {response.status_code}"
    )


@pytest.mark.parametrize(
    "other_kind", ["same-email-new-subject", "same-subject-new-issuer", "same-subject-new-email"]
)
def test_stable_principal_private_access(session_app, tmp_path, other_kind):
    web, service, client = session_app
    issuer = "https://issuer.test"
    claims = {"sub": "stable-owner", "email": "owner@one.example"}
    owner_login = auth.OidcLogin(issuer, "client", "unused", admins_file=tmp_path / "admins")
    owner = owner_login.account(claims)
    client.cookies.set(web.COOKIE, web.signer.issue(owner))
    plan = copy.deepcopy(service.catalog()[0]["plan"])
    plan["id"] = "stable-private-" + uuid4().hex
    assert (
        client.post(
            "/api/workbench/plans", json={"plan": plan}, headers={"origin": "https://testserver"}
        ).status_code
        == 200
    )
    if other_kind == "same-email-new-subject":
        claims["sub"] = "independent-subject"
    elif other_kind == "same-subject-new-issuer":
        issuer = "https://other-issuer.test"
    else:
        claims["email"] = "renamed@two.example"
    new_login = auth.OidcLogin(issuer, "client", "unused", admins_file=tmp_path / "admins")
    client.cookies.clear()
    client.cookies.set(web.COOKIE, web.signer.issue(new_login.account(claims)))
    response = client.get("/api/workbench/plans/" + plan["id"])
    assert response.status_code == (200 if other_kind == "same-subject-new-email" else 404)


def test_legacy_cookie_cannot_access_private_records(session_app):
    import json
    import time

    web, service, client = session_app
    body = {"u": "tester-local", "p": "auditor", "n": "Legacy", "exp": int(time.time()) + 300}
    payload = auth._b64(json.dumps(body).encode())
    client.cookies.set(web.COOKIE, f"{payload}.{web.signer._mac(payload)}")
    assert client.get("/api/workbench/bootstrap").status_code == 401
    assert (
        client.post(
            "/api/workbench/identities/challenge", json={}, headers={"origin": "https://testserver"}
        ).status_code
        == 401
    )


def test_production_namespace_quarantines_legacy_authority(tmp_path):
    import base64
    import hashlib

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from server import app as web

    # Production's composition root must never open the legacy root as active storage.
    assert web.workbench.directory.name == auth.WORKBENCH_AUTH_NAMESPACE
    legacy = WorkbenchAPI(tmp_path, lambda r: {"username": "tester-local"})
    plan = copy.deepcopy(legacy.catalog()[0]["plan"])
    legacy.store.save_plan("tester-local", plan, "legacy-digest", create=True)
    public = base64.b64encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw()).decode()
    legacy.governance.enroll_identity("tester-local", public)
    # Retain the raw legacy database files, including any signed payloads, byte-for-byte.
    before = {p: hashlib.sha256(p.read_bytes()).digest() for p in tmp_path.iterdir() if p.is_file()}
    current = WorkbenchAPI(tmp_path / auth.WORKBENCH_AUTH_NAMESPACE, legacy.user_of)
    app = FastAPI()
    app.include_router(current.router)
    with TestClient(app) as client:
        assert client.get("/api/workbench/plans/" + plan["id"]).status_code == 404
        assert client.get("/api/workbench/bootstrap").json()["plans"] == []
    with current.governance._db() as db:
        assert current.governance._get(db, "identity", "tester-local", False) is None
    assert {p: hashlib.sha256(p.read_bytes()).digest() for p in before} == before
