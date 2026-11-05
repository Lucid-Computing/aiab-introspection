"""Confidential ASGI runtime endpoint, with external admission and release authorities.

create_app is the deployment seam. Keys, bearer authentication, governance and
receipt signing are never exposed to Pi. There is no debug/raw-result HTTP route.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import secrets
import sqlite3
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import FastAPI, HTTPException, Request

from aiab.workbench.runner import execute_plan
from aiab.workbench.tools import RuntimeFailure


async def invoke(fn, *args, **kwargs):
    value = fn(*args, **kwargs)
    return await value if inspect.isawaitable(value) else value


@dataclass(frozen=True)
class Principal:
    id: str
    role: str
    capabilities: frozenset[str]


class SealedInputs:
    """X25519/HKDF-SHA256/AES256-GCM envelope v1.

    Caller obtains this public key through the measured enclave's authenticated
    channel. This envelope alone is NOT attestation. External key management owns
    private bytes and rotation. Replay protection is the durable service ledger.
    """

    def __init__(self, private_key: X25519PrivateKey):
        self._key = private_key

    def public_key(self):
        return base64.b64encode(self._key.public_key().public_bytes_raw()).decode()

    def open(self, envelope, aad):
        try:
            if set(envelope) != {"version", "ephemeral_key", "nonce", "ciphertext"}:
                raise ValueError
            if envelope["version"] != "aiab-sealed-input/v1":
                raise ValueError

            def decode(value):
                return base64.b64decode(value, validate=True)

            peer, nonce, ciphertext = (
                decode(envelope[k]) for k in ("ephemeral_key", "nonce", "ciphertext")
            )
            if len(nonce) != 12 or not 16 <= len(ciphertext) <= 16 * 1024**2:
                raise ValueError
            shared = self._key.exchange(X25519PublicKey.from_public_bytes(peer))
            key = HKDF(
                algorithm=hashes.SHA256(), length=32, salt=None, info=b"aiab-sealed-input/v1"
            ).derive(shared)
            plain = AESGCM(key).decrypt(nonce, ciphertext, aad)
            value = json.loads(plain)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except Exception:
            raise RuntimeFailure("invalid_sealed_input") from None


def create_app(
    *,
    authenticate: Callable,
    admit: Callable,
    release: Callable,
    ledger_path: str,
    result_key: bytes,
    sealed_inputs: SealedInputs | None = None,
    max_concurrent: int = 4,
    execute: Callable = execute_plan,
):
    """Deploy with uvicorn using an operator-owned factory that calls this function.

    authenticate(bearer) -> Principal: authenticate externally issued capabilities.
    admit(principal, plan, dataset) -> trusted actor dict: MUST atomically authorize
      exact plan/data and return id, authorized_dataset_ids, authorized_subjects;
      it may additionally attach governance_run_id for release accounting.
    release(principal, governance_run_id, private_result) -> recipient-safe object:
      MUST revalidate authorization/revocation and atomically account disclosure.
    result_key is 32 bytes from the external key service (not agent-visible).
    The internal encrypted result ledger is not a signed receipt. The release
    authority owns receipt signing. A single service process owns a ledger.
    execute injection is only for service tests, never an HTTP option.
    """
    if len(result_key) != 32 or not 1 <= max_concurrent <= 32:
        raise ValueError("invalid_service_config")
    cipher = AESGCM(result_key)
    db = sqlite3.connect(ledger_path, isolation_level=None, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("""CREATE TABLE IF NOT EXISTS runtime_runs (
        id TEXT PRIMARY KEY, owner TEXT NOT NULL, status TEXT NOT NULL,
        submission TEXT UNIQUE NOT NULL, governance_id TEXT, result BLOB, error TEXT)""")
    # Restart never replays inference or pending inputs. Incomplete work fails closed.
    db.execute(
        "UPDATE runtime_runs SET status='interrupted',error='service_restarted' "
        "WHERE status IN ('admitting','queued','running')"
    )
    tasks = {}

    @asynccontextmanager
    async def lifespan(app):
        yield
        for task in list(tasks.values()):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks.values(), return_exceptions=True)
        db.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    async def principal(request, capability):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer ") or len(header) > 8192:
            raise HTTPException(401, "authentication_required")
        try:
            who = await invoke(authenticate, header[7:])
        except Exception:
            raise HTTPException(401, "invalid_capability") from None
        if (
            not isinstance(who, Principal)
            or who.role not in {"owner", "auditor", "runtime"}
            or capability not in who.capabilities
        ):
            raise HTTPException(403, "capability_required")
        return who

    def owned(run_id, who):
        row = db.execute(
            "SELECT owner,status,governance_id,result,error FROM runtime_runs WHERE id=?", (run_id,)
        ).fetchone()
        if row is None or row[0] != who.id:
            raise HTTPException(404, "run_not_found")
        return row

    async def perform(run_id, plan, dataset, actor):
        try:
            db.execute("UPDATE runtime_runs SET status='running' WHERE id=?", (run_id,))
            result = await execute(plan, dataset, actor=actor)
            nonce = secrets.token_bytes(12)
            encrypted = nonce + cipher.encrypt(nonce, json.dumps(result).encode(), run_id.encode())
            db.execute(
                "UPDATE runtime_runs SET status='completed',result=? WHERE id=?",
                (encrypted, run_id),
            )
        except asyncio.CancelledError:
            db.execute(
                "UPDATE runtime_runs SET status='cancelled',result=NULL WHERE id=?", (run_id,)
            )
            raise
        except Exception as exc:
            # Only fixed runtime codes leave the confidential process.
            code = str(exc) if isinstance(exc, RuntimeFailure) else "runtime_failed"
            db.execute(
                "UPDATE runtime_runs SET status='failed',error=?,result=NULL WHERE id=?",
                (code, run_id),
            )
        finally:
            tasks.pop(run_id, None)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            {"error": {"code": exc.detail, "message": exc.detail}}, status_code=exc.status_code
        )

    @app.post("/v1/runs")
    async def submit(request: Request):
        who = await principal(request, "runtime:submit")
        if len(tasks) >= max_concurrent:
            raise HTTPException(429, "runtime_capacity_exhausted")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 24 * 1024**2:
                raise HTTPException(413, "payload_too_large")
        try:
            body = json.loads(raw)
            if not isinstance(body, dict) or set(body) - {
                "sealed_input",
                "request_id",
                "plan",
                "dataset",
            }:
                raise ValueError
            request_id = body["request_id"]
            if not isinstance(request_id, str) or not 16 <= len(request_id) <= 128:
                raise ValueError
            submission = hashlib.sha256((who.id + "\0" + request_id).encode()).hexdigest()
            if db.execute(
                "SELECT 1 FROM runtime_runs WHERE submission=?", (submission,)
            ).fetchone():
                raise HTTPException(409, "submission_replayed")
            if "sealed_input" in body:
                if set(body) != {"request_id", "sealed_input"} or sealed_inputs is None:
                    raise ValueError
                aad = json.dumps(
                    {"principal": who.id, "request_id": request_id},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                payload = sealed_inputs.open(body["sealed_input"], aad)
            else:
                payload = body
            plan, dataset = payload["plan"], payload["dataset"]
            if plan["mode"] == "confidential" and "sealed_input" not in body:
                raise HTTPException(400, "confidential_input_must_be_sealed")
            # Reserve a durable slot/replay key before the external admission await.
            # Concurrent identical requests cannot each consume governance admission.
            active = db.execute(
                "SELECT count(*) FROM runtime_runs WHERE status IN ('admitting','queued','running')"
            ).fetchone()[0]
            if active >= max_concurrent:
                raise HTTPException(429, "runtime_capacity_exhausted")
            run_id = secrets.token_hex(24)
            db.execute(
                "INSERT INTO runtime_runs(id,owner,status,submission) VALUES(?,?,?,?)",
                (run_id, who.id, "admitting", submission),
            )
            try:
                actor = await invoke(admit, who, plan, dataset)
                if not isinstance(actor, dict) or actor.get("id") != who.id:
                    raise HTTPException(403, "admission_denied")
                db.execute(
                    "UPDATE runtime_runs SET status='queued',governance_id=? WHERE id=?",
                    (actor.get("governance_run_id", run_id), run_id),
                )
            except BaseException:
                db.execute(
                    "UPDATE runtime_runs SET status='rejected',error='admission_denied' WHERE id=?",
                    (run_id,),
                )
                raise
        except HTTPException:
            raise
        except sqlite3.IntegrityError:
            raise HTTPException(409, "submission_replayed") from None
        except Exception:
            raise HTTPException(400, "admission_or_payload_invalid") from None
        tasks[run_id] = asyncio.create_task(perform(run_id, plan, dataset, actor))
        return {"run_id": run_id, "status": "queued"}

    @app.get("/v1/runs/{run_id}")
    async def status(run_id: str, request: Request):
        who = await principal(request, "runtime:status")
        row = owned(run_id, who)
        return {"run_id": run_id, "status": row[1], "error": row[4]}

    @app.post("/v1/runs/{run_id}/cancel")
    async def cancel(run_id: str, request: Request):
        who = await principal(request, "runtime:cancel")
        owned(run_id, who)
        task = tasks.get(run_id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return {"run_id": run_id, "status": owned(run_id, who)[1]}

    @app.post("/v1/runs/{run_id}/release")
    async def release_result(run_id: str, request: Request):
        who = await principal(request, "runtime:release")
        row = owned(run_id, who)
        if row[1] != "completed" or row[3] is None:
            raise HTTPException(409, "result_unavailable")
        try:
            blob = row[3]
            result = json.loads(cipher.decrypt(blob[:12], blob[12:], run_id.encode()))
            safe = await invoke(release, who, row[2], result)
            if not isinstance(safe, dict):
                raise ValueError
            # The authority is responsible for nested projections; top-level trace is never legal.
            if set(safe) & {"trace", "messages", "raw_events", "private_result"}:
                raise ValueError
        except Exception:
            raise HTTPException(403, "release_denied") from None
        return {"run_id": run_id, "result": safe}

    @app.get("/v1/input-key")
    async def input_key(request: Request):
        await principal(request, "runtime:submit")
        if sealed_inputs is None:
            raise HTTPException(503, "sealed_input_unavailable")
        return {
            "version": "aiab-sealed-input/v1",
            "public_key": sealed_inputs.public_key(),
            "attestation_required": True,
        }

    return app
