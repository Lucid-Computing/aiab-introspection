"""Authenticated workbench API; synthetic rehearsals are explicitly not consented audits."""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import secrets
import time
from functools import lru_cache
from pathlib import Path

from aiab.workbench import contracts, datasets
from aiab.workbench.evidence import EvidenceStore
from aiab.workbench.governance import GovernanceStore, canonical
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from server.workbench_store import Conflict, WorkbenchStore


class APIError(ValueError):
    def __init__(self, code, message, status=400):
        self.code, self.status = code, status
        super().__init__(message)


class JSONRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                return await original(request)
            except APIError as exc:
                code, status, message = exc.code, exc.status, str(exc)
            except Conflict as exc:
                code, status, message = "conflict", 409, str(exc)
            except KeyError:
                code, status, message = "not_found", 404, "Resource not found."
            except (ValueError, TypeError) as exc:
                code, status, message = "invalid_request", 400, str(exc)
            return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)

        return handler


@lru_cache(maxsize=1)
def demo_dataset():
    return contracts.validate_dataset(datasets.generate())


class WorkbenchAPI:
    def __init__(self, directory: Path, user_of):
        self.directory, self.user_of = Path(directory), user_of
        self.store = WorkbenchStore(self.directory / "workbench.db")
        self.governance = GovernanceStore(self.directory / "governance.db")
        self.tasks = {}
        self.challenges = {}
        self.router = APIRouter(prefix="/api/workbench", route_class=JSONRoute)
        self._routes()

    def actor(self, request):
        user = self.user_of(request)
        if not user:
            raise APIError("authentication_required", "Sign in to use the workbench.", 401)
        if request.method not in {"GET", "HEAD"}:
            # JSON mutations require an exact same-origin browser request. Neither cookies
            # nor a caller-supplied actor grants a cross-origin write capability.
            origin = request.headers.get("origin")
            expected = str(request.base_url).rstrip("/")
            if origin != expected:
                raise APIError("origin_denied", "Same-origin request required.", 403)
        return user["username"]

    async def body(self, request):
        buf = bytearray()
        async for chunk in request.stream():
            buf.extend(chunk)
            if len(buf) > 32 * 1024 * 1024:
                raise APIError("too_large", "Request exceeds 32 MiB; use partitioned import.", 413)
        value = json.loads(buf or b"{}")
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object")
        return value

    def dataset(self, actor, ident):
        if ident == "northstar":
            return copy.deepcopy(demo_dataset())
        return self.store.dataset(actor, ident)

    def accessible_plan(self, actor, ident):
        try:
            return self.store.plan(actor, ident)
        except KeyError:
            try:
                visible = self.governance.plans(actor)
            except ValueError:
                visible = []
            matches = [p for p in visible if p["plan"]["id"] == ident]
            if len(matches) == 1:
                return matches[0]
            if matches:
                raise Conflict(
                    "Several agreements use this ID; ask the owner for a unique ID."
                ) from None
            raise

    def catalog(self):
        data = demo_dataset()
        rows = []
        for descriptor in datasets.catalog():
            plan = datasets.plan_for(descriptor["id"])
            # Templates describe an explicit synthetic universe, visible before signing.
            plan["scope"]["subjects"] = sorted({e["actor"] for e in data["events"] if e["actor"]})
            plan["scope"]["fields"] = list(contracts.EVENT_FIELDS)
            plan["scope"]["sources"] = sorted({e["source"] for e in data["events"]})
            rows.append(dict(descriptor, plan=plan))
        return rows

    def binding(self, actor, plan):
        """Only operator-installed grants authorize native data; HTTP cannot mint them."""
        path = self.directory / "source-grants.json"
        grants = json.loads(path.read_text()) if path.exists() else []
        for grant in grants:
            if grant.get("owner") == actor and grant.get("plan_digest") == contracts.plan_digest(
                plan
            ):
                return grant
        raise APIError(
            "source_authorization_required",
            "An independently verified source grant is required.",
            403,
        )

    def bind(self, actor, plan):
        grant = self.binding(actor, plan)
        if len(plan["scope"]["dataset_ids"]) != 1:
            raise ValueError("Assemble sources into one committed corpus before admission.")
        dataset = self.dataset(actor, plan["scope"]["dataset_ids"][0])
        manifest = {
            "dataset_digest": contracts.dataset_digest(dataset),
            "manifest": dataset["manifest"],
        }
        self.governance.register_plan(
            plan,
            actor,
            dataset_manifest=manifest,
            authorization=grant["authorization"],
            roster_id=grant.get("roster_id"),
        )
        return grant, manifest

    async def execute(self, actor, run_id):
        if not self.store.claim(actor, run_id):
            return
        job = self.store.job(actor, run_id)
        try:
            from aiab.workbench.runner import execute_plan

            if job["governance_id"]:
                self.governance.revalidate(job["governance_id"])
            result = await execute_plan(
                job["plan"], self.dataset(actor, job["dataset_id"]), actor=actor
            )
            if job["governance_id"]:
                self.governance.revalidate(job["governance_id"])
            self.store.finish(actor, run_id, result=result)
        except asyncio.CancelledError:
            self.store.cancel(actor, run_id)
            raise
        except Exception:
            # Model/provider errors can contain sensitive request bodies. Keep them off HTTP.
            self.store.finish(
                actor, run_id, error="Execution failed; no completed result was released."
            )
        finally:
            self.tasks.pop(run_id, None)

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _routes(self):
        router = self.router

        @router.get("/bootstrap")
        async def bootstrap(request: Request):
            actor = self.actor(request)
            demo = {k: v for k, v in demo_dataset().items() if k != "events"}
            return {
                "user": dict(self.user_of(request), id=actor),
                "audits": self.catalog(),
                "datasets": [demo, *self.store.datasets(actor)],
                "plans": self.store.plans(actor),
                "capabilities": {
                    "synthetic_rehearsal": True,
                    "signed_consent": True,
                    "confidential_submission": False,
                    "model_provider": "tinfoil",
                    "model": "deepseek-v4-1-flash",
                },
            }

        @router.get("/catalog")
        async def catalog(request: Request):
            self.actor(request)
            return {"audits": self.catalog()}

        @router.get("/datasets")
        async def list_datasets(request: Request):
            actor = self.actor(request)
            return {
                "datasets": [
                    {k: v for k, v in demo_dataset().items() if k != "events"},
                    *self.store.datasets(actor),
                ]
            }

        @router.post("/datasets")
        async def import_dataset(request: Request):
            actor = self.actor(request)
            data = contracts.validate_dataset((await self.body(request))["dataset"])
            if data["id"] == "northstar":
                raise Conflict("The built-in synthetic dataset cannot be replaced.")
            self.store.add_dataset(actor, data)
            return {"dataset": {k: v for k, v in data.items() if k != "events"}}

        @router.get("/plans")
        async def plans(request: Request):
            return {"plans": self.store.plans(self.actor(request))}

        @router.get("/plans/{ident}")
        async def plan(request: Request, ident: str):
            return dict(self.accessible_plan(self.actor(request), ident), approvals=[])

        @router.post("/plans")
        async def create(request: Request):
            actor = self.actor(request)
            plan = contracts.validate_plan((await self.body(request))["plan"])
            return self.store.save_plan(actor, plan, contracts.plan_digest(plan), create=True)

        @router.put("/plans/{ident}")
        async def update(request: Request, ident: str):
            actor, body = self.actor(request), await self.body(request)
            plan = contracts.validate_plan(body["plan"])
            if plan["id"] != ident:
                raise ValueError("Plan ID cannot change during update")
            return self.store.save_plan(
                actor, plan, contracts.plan_digest(plan), expected_digest=body["expected_digest"]
            )

        @router.post("/check")
        async def check(request: Request):
            actor, body = self.actor(request), await self.body(request)
            try:
                plan = contracts.validate_plan(body["plan"])
                for ident in plan["scope"]["dataset_ids"]:
                    EvidenceStore(self.dataset(actor, ident), plan["scope"])
                if not plan["scope"]["dataset_ids"]:
                    raise ValueError("Select an explicit input dataset")
            except ValueError as exc:
                return {"valid": False, "diagnostics": [str(exc)], "requirements": {}}
            return {
                "valid": True,
                "diagnostics": [],
                "requirements": {
                    "audit": "Verified source grant, signed consent, and release policy",
                    "agent": "Verified DeepSeek/Tinfoil inference; no fallback",
                },
            }

        @router.post("/identities/challenge")
        async def identity_challenge(request: Request):
            actor, body = self.actor(request), await self.body(request)
            key = body["public_key"]
            Ed25519PublicKey.from_public_bytes(base64.b64decode(key, validate=True))
            self.challenges = {
                k: v for k, v in self.challenges.items() if v["expires"] > time.time()
            }
            if len(self.challenges) >= 1000:
                raise APIError("busy", "Too many outstanding enrollment challenges.", 429)
            ident = secrets.token_hex(32)
            payload = canonical(
                {
                    "purpose": "workbench-key-enrollment/v1",
                    "actor": actor,
                    "public_key": key,
                    "nonce": ident,
                }
            )
            self.challenges[ident] = {
                "actor": actor,
                "key": key,
                "payload": payload,
                "expires": time.time() + 120,
            }
            return {"challenge_id": ident, "payload": base64.b64encode(payload).decode()}

        @router.post("/identities/enroll")
        async def enroll(request: Request):
            actor, body = self.actor(request), await self.body(request)
            item = self.challenges.pop(body["challenge_id"], None)
            if not item or item["actor"] != actor or item["expires"] < time.time():
                raise ValueError("Invalid or expired enrollment challenge")
            try:
                Ed25519PublicKey.from_public_bytes(base64.b64decode(item["key"])).verify(
                    base64.b64decode(body["signature"], validate=True), item["payload"]
                )
            except InvalidSignature as exc:
                raise ValueError("Invalid enrollment signature") from exc
            self.governance.enroll_identity(actor, item["key"])
            return {"enrolled": True}

        async def saved(request, ident):
            actor, body = self.actor(request), await self.body(request)
            package = self.accessible_plan(actor, ident)
            if body.get("digest") != package["digest"]:
                raise Conflict("Approval must refer to the current saved digest")
            return actor, body, package

        @router.post("/plans/{ident}/approval-payload")
        async def approval_payload(request: Request, ident: str):
            actor, body, package = await saved(request, ident)
            try:
                self.store.plan(actor, ident)
            except KeyError:
                pass  # A participant signs the owner's already frozen binding.
            else:
                self.bind(actor, package["plan"])
            payload = self.governance.approval_payload(
                package["digest"], actor, nonce=body["nonce"], expires_at=body.get("expires_at")
            )
            return {"payload": base64.b64encode(canonical(payload)).decode()}

        @router.post("/plans/{ident}/approve")
        async def approve(request: Request, ident: str):
            actor, body, package = await saved(request, ident)
            return self.governance.approve(
                package["digest"],
                actor,
                signature=body["signature"],
                nonce=body["nonce"],
                expires_at=body.get("expires_at"),
            )

        @router.post("/plans/{ident}/revoke")
        async def revoke(request: Request, ident: str):
            actor, _, package = await saved(request, ident)
            return self.governance.revoke(package["digest"], actor)

        @router.post("/runs")
        async def run(request: Request):
            actor, body = self.actor(request), await self.body(request)
            package = self.store.plan(actor, body["plan_id"])
            plan, mode = package["plan"], body["mode"]
            data = self.dataset(actor, body["dataset_id"])
            EvidenceStore(data, plan["scope"])
            if len(self.tasks) >= 4:
                raise APIError("busy", "Four investigations are already running.", 429)
            governance_id = None
            if mode == "rehearse":
                # Only the server-generated fixture qualifies. Imported origin labels
                # are assertions, never authority to bypass consent.
                if data["id"] != "northstar":
                    raise APIError(
                        "consent_required", "Rehearsal uses the built-in synthetic corpus.", 403
                    )
            elif mode == "audit":
                if plan["mode"] == "confidential":
                    raise APIError(
                        "sealed_runtime_required", "Use a verified sealed-input runtime.", 403
                    )
                grant, manifest = self.bind(actor, plan)
                governance_id = self.governance.admit(
                    plan,
                    actor,
                    manifest,
                    request_id=secrets.token_hex(24),
                    authorization=grant["authorization"],
                )["run_id"]
            else:
                raise ValueError("Unknown run mode")
            run_id = self.store.enqueue(
                actor, plan, package["digest"], data["id"], mode, governance_id=governance_id
            )
            self.tasks[run_id] = asyncio.create_task(self.execute(actor, run_id))
            return {"run_id": run_id}

        @router.get("/runs/{ident}")
        async def result(request: Request, ident: str):
            actor = self.actor(request)
            job = self.store.job(actor, ident)
            out = {
                "run_id": ident,
                "status": job["status"],
                "mode": job["mode"],
                "plan_digest": job["digest"],
                "error": job["error"],
            }
            if job["result"] is not None:
                if job["mode"] == "rehearse":
                    out["result"] = job["result"]
                    out["verification"] = {"origin": "synthetic", "consented_audit": False}
                else:
                    grant = self.binding(actor, job["plan"])
                    package = self.governance.release(
                        job["governance_id"],
                        job["result"],
                        actor,
                        actor=actor,
                        authorization=grant["authorization"],
                    )
                    out.update(result=package["projection"], receipt=package["receipt"])
            return out

        @router.post("/runs/{ident}/cancel")
        async def cancel(request: Request, ident: str):
            actor = self.actor(request)
            job = self.store.job(actor, ident)
            if job["governance_id"]:
                self.governance.cancel(job["governance_id"], actor)
            self.store.cancel(actor, ident)
            if ident in self.tasks:
                self.tasks[ident].cancel()
            return {"status": self.store.job(actor, ident)["status"]}

        @router.get("/history")
        async def history(request: Request):
            return {"history": self.governance.history(self.actor(request))}
