import asyncio
import base64
import json
import sqlite3

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from aiab.workbench.service import Principal, SealedInputs, create_app
from aiab.workbench.tools import RuntimeFailure


def envelope(private, payload, aad):
    ephemeral = X25519PrivateKey.generate()
    shared = ephemeral.exchange(private.public_key())
    key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=b"aiab-sealed-input/v1"
    ).derive(shared)
    nonce = b"012345678901"

    def encode(b):
        return base64.b64encode(b).decode()

    return {
        "version": "aiab-sealed-input/v1",
        "ephemeral_key": encode(ephemeral.public_key().public_bytes_raw()),
        "nonce": encode(nonce),
        "ciphertext": encode(AESGCM(key).encrypt(nonce, json.dumps(payload).encode(), aad)),
    }


def test_sealed_input_authentication_and_aad():
    private = X25519PrivateKey.generate()
    sealed = SealedInputs(private)
    e = envelope(private, {"plan": {}, "dataset": {}}, b"bound-principal-and-request")
    assert sealed.open(e, b"bound-principal-and-request") == {"plan": {}, "dataset": {}}
    with pytest.raises(RuntimeFailure, match="invalid_sealed_input"):
        sealed.open(e, b"wrong-principal")
    e["ciphertext"] = e["ciphertext"][:-4] + "AAAA"
    with pytest.raises(RuntimeFailure, match="invalid_sealed_input"):
        sealed.open(e, b"bound-principal-and-request")


@pytest.fixture
def service(tmp_path):
    private = X25519PrivateKey.generate()
    ledger = tmp_path / "ledger.db"
    release_calls = []
    cancel_entered = asyncio.Event()
    released = {"findings": [{"claim": "safe aggregate"}]}

    def authenticate(token):
        if token not in {"alice", "bob", "employer"}:
            raise ValueError
        return Principal(
            token,
            "owner" if token != "employer" else "employer",
            frozenset({"runtime:submit", "runtime:status", "runtime:cancel", "runtime:release"}),
        )

    def admit(who, plan, dataset):
        return {
            "id": who.id,
            "authorized_dataset_ids": ["data"],
            "authorized_subjects": [who.id],
            "governance_run_id": "admitted-run",
        }

    def release(who, run_id, result):
        release_calls.append((who.id, run_id))
        assert result["trace"][0]["raw"] == "PRIVATE-SENTINEL"
        return released

    async def execute(plan, dataset, **kwargs):
        if plan.get("wait"):
            cancel_entered.set()
            await asyncio.sleep(60)
        return {"findings": [], "trace": [{"raw": "PRIVATE-SENTINEL"}]}

    app = create_app(
        authenticate=authenticate,
        admit=admit,
        release=release,
        execute=execute,
        ledger_path=str(ledger),
        result_key=b"x" * 32,
        sealed_inputs=SealedInputs(private),
    )
    return app, private, ledger, release_calls, cancel_entered, released


async def test_auth_sealed_submission_encrypted_private_result_and_release(service):
    app, private, ledger, release_calls, _, _ = service
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://runtime"
        ) as client:
            payload = {"plan": {"mode": "confidential"}, "dataset": {"id": "data"}}
            body = {"request_id": "request-000000001", **payload}
            assert (await client.post("/v1/runs", json=body)).status_code == 401
            headers = {"authorization": "Bearer alice"}
            assert (await client.post("/v1/runs", headers=headers, json=body)).status_code == 400
            aad = b'{"principal":"alice","request_id":"request-000000001"}'
            body = {
                "request_id": "request-000000001",
                "sealed_input": envelope(private, payload, aad),
            }
            response = await client.post("/v1/runs", headers=headers, json=body)
            assert response.status_code == 200, response.text
            run_id = response.json()["run_id"]
            await asyncio.sleep(0.02)
            status = await client.get("/v1/runs/" + run_id, headers=headers)
            assert status.json()["status"] == "completed"
            assert "PRIVATE" not in status.text
            denied = await client.get("/v1/runs/" + run_id, headers={"authorization": "Bearer bob"})
            assert denied.status_code == 404
            denied = await client.post(
                "/v1/runs", json=body, headers={"authorization": "Bearer employer"}
            )
            assert denied.status_code == 403
            assert (await client.post("/v1/runs", headers=headers, json=body)).status_code == 409
            released = await client.post("/v1/runs/" + run_id + "/release", headers=headers)
            assert released.status_code == 200 and "PRIVATE" not in released.text
            assert release_calls == [("alice", "admitted-run")]
    assert b"PRIVATE-SENTINEL" not in ledger.read_bytes()


async def test_cancel_and_private_release_denial(service):
    app, _, _, _, entered, released = service
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://runtime"
        ) as client:
            headers = {"authorization": "Bearer alice"}
            body = {
                "request_id": "request-000000002",
                "plan": {"mode": "personal", "wait": True},
                "dataset": {},
            }
            run = (await client.post("/v1/runs", json=body, headers=headers)).json()["run_id"]
            await asyncio.wait_for(entered.wait(), 1)
            result = await client.post("/v1/runs/" + run + "/cancel", headers=headers)
            assert result.json()["status"] == "cancelled"
            assert (
                await client.post("/v1/runs/" + run + "/release", headers=headers)
            ).status_code == 409
            body["request_id"] = "request-000000003"
            body["plan"]["wait"] = False
            run = (await client.post("/v1/runs", json=body, headers=headers)).json()["run_id"]
            await asyncio.sleep(0.02)
            released["trace"] = ["must never leave"]
            assert (
                await client.post("/v1/runs/" + run + "/release", headers=headers)
            ).status_code == 403


async def test_concurrent_duplicate_reserves_before_async_admission(tmp_path):
    entered, unblock = asyncio.Event(), asyncio.Event()
    calls = []

    def auth(token):
        return Principal("alice", "owner", frozenset({"runtime:submit"}))

    async def admit(who, plan, dataset):
        calls.append(who.id)
        entered.set()
        await unblock.wait()
        return {"id": who.id}

    async def execute(*args, **kwargs):
        return {"findings": [], "trace": []}

    app = create_app(
        authenticate=auth,
        admit=admit,
        release=lambda *a: {},
        execute=execute,
        ledger_path=str(tmp_path / "duplicate.db"),
        result_key=b"x" * 32,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://runtime"
        ) as client:
            body = {
                "request_id": "duplicate-request-0001",
                "plan": {"mode": "personal"},
                "dataset": {},
            }
            headers = {"authorization": "Bearer test"}
            first = asyncio.create_task(client.post("/v1/runs", json=body, headers=headers))
            await asyncio.wait_for(entered.wait(), 1)
            second = await client.post("/v1/runs", json=body, headers=headers)
            assert second.status_code == 409
            unblock.set()
            assert (await first).status_code == 200
            assert calls == ["alice"]


async def test_restart_fails_closed_for_unfinished_run(tmp_path):
    ledger = tmp_path / "restart.db"

    def config():
        return dict(
            authenticate=lambda _: Principal("alice", "owner", frozenset({"runtime:status"})),
            admit=lambda *a: {"id": "alice"},
            release=lambda *a: {},
            ledger_path=str(ledger),
            result_key=b"x" * 32,
        )

    app = create_app(**config())
    async with app.router.lifespan_context(app):
        with sqlite3.connect(ledger) as connection:
            connection.execute(
                "INSERT INTO runtime_runs(id,owner,status,submission) VALUES(?,?,?,?)",
                ("old-run", "alice", "running", "old-submission"),
            )
    restarted = create_app(**config())
    async with restarted.router.lifespan_context(restarted):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted), base_url="https://runtime"
        ) as client:
            result = await client.get("/v1/runs/old-run", headers={"authorization": "Bearer test"})
            assert result.json() == {
                "run_id": "old-run",
                "status": "interrupted",
                "error": "service_restarted",
            }
