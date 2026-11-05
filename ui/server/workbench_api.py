"""Authenticated workbench API; synthetic rehearsals are explicitly not consented audits."""

from __future__ import annotations

import asyncio
import base64
import copy
import inspect
import json
import secrets
import time
from functools import lru_cache
from pathlib import Path

from aiab.workbench import contracts, datasets
from aiab.workbench.evidence import EvidenceStore
from aiab.workbench.governance import GovernanceStore, canonical, commitment
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
            except (ValueError, TypeError):
                code, status, message = "invalid_request", 400, "Request invalid or unavailable."
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

    def agreements(self, actor):
        # Governance discovery is authorization-filtered, never a raw ledger query.
        visible = self.governance.plans(actor)
        current = []
        for package in visible:
            try:
                self.store.current_owner(package["digest"])
            except KeyError:
                continue
            current.append(package)
        own = self.store.plans(actor)
        return own + [p for p in current if p["digest"] not in {v["digest"] for v in own}]

    def accessible_plan(self, actor, ident):
        matches = [p for p in self.agreements(actor) if p["plan"]["id"] == ident]
        if len(matches) > 1:
            raise Conflict("Several agreements use this ID; ask for a unique ID.")
        if not matches:
            raise KeyError("Agreement not found")
        return matches[0]

    def runtime_check(self, plan):
        from aiab.workbench.runner import budgets, graph_order, validate_operators

        budgets(plan)
        validate_operators(graph_order(plan["steps"]), live=True)

    def run_header(self, actor, ident, *, participant=False):
        header = self.store.job_header(ident)
        if actor == header["owner"]:
            return header
        if header["mode"] != "audit":
            raise KeyError("Run not found")
        visible = {
            p["digest"]
            for p in (self.governance.plans(actor) if participant else self.agreements(actor))
        }
        recipients = {r["id"] for r in header["plan"]["disclosure"]["recipients"]}
        if header["digest"] not in visible or (not participant and actor not in recipients):
            raise KeyError("Run not found")
        return header

    def revalidate_job(self, header):
        if not self.store.is_current(header["owner"], header["plan"]):
            raise ValueError("Agreement superseded")
        # Re-read operator authorization and full input commitments at each available
        # runtime checkpoint. Removed/changed grants fail closed, not just expired grants.
        self.bind(header["owner"], header["plan"])
        self.governance.revalidate(header["governance_id"])

    def release_for(self, actor, ident):
        header = self.run_header(actor, ident)
        if header["mode"] != "audit":
            raise ValueError("Synthetic runs do not have governed releases")
        self.revalidate_job(header)
        try:
            status = self.governance.run_status(header["governance_id"], actor)
        except ValueError:
            status = {"releases": []}  # first release to an authorized recipient
        if status["releases"]:
            return status["releases"][0]
        job = self.store.job(header["owner"], ident)
        if job["status"] != "completed" or job["result"] is None:
            raise ValueError("Result unavailable")
        grant = self.binding(header["owner"], header["plan"])
        return self.governance.release(
            header["governance_id"],
            job["result"],
            actor,
            actor=actor,
            authorization=grant["authorization"],
        )

    @staticmethod
    def permitted(plan):
        return (
            set(plan["scope"]["subjects"])
            | set(plan["approval"]["required_signers"])
            | {r["id"] for r in plan["disclosure"]["recipients"]}
        )

    def catalog(self):
        data = demo_dataset()
        rows = []
        for descriptor in datasets.catalog():
            plan = datasets.plan_for(descriptor["id"])
            # Templates describe an explicit synthetic universe, visible before signing.
            plan["scope"]["subjects"] = sorted({e["actor"] for e in data["events"] if e["actor"]})
            plan["scope"]["fields"] = list(contracts.EVENT_FIELDS)
            plan["scope"]["sources"] = sorted({e["source"] for e in data["events"]})
            try:
                self.runtime_check(plan)
                validation = {"valid": True, "diagnostics": []}
            except (ValueError, RuntimeError):
                validation = {
                    "valid": False,
                    "diagnostics": ["Runtime operator configuration unavailable."],
                }
            rows.append(dict(descriptor, plan=plan, runtime_validation=validation))
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
        if "dataset_manifest" in grant and grant["dataset_manifest"] != manifest:
            raise ValueError("Source manifest mismatch")
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

            data = self.dataset(actor, job["dataset_id"])
            if job["governance_id"]:
                self.revalidate_job(job)
                auth = self.binding(actor, job["plan"])["authorization"]
            else:
                if data["id"] != "northstar":
                    raise ValueError("Synthetic fixture required")
                auth = {
                    "dataset_ids": ["northstar"],
                    "subjects": sorted(
                        {e["actor"] for e in demo_dataset()["events"] if e["actor"]}
                    ),
                }
            trusted_actor = {
                "id": actor,
                "authorized_dataset_ids": auth["dataset_ids"],
                "authorized_subjects": auth["subjects"],
            }

            async def checkpoint(_private_event):
                if job["governance_id"]:
                    self.revalidate_job(job)

            async def authorize_access():
                if self.store.job(actor, run_id)["status"] != "running":
                    raise ValueError("Run is no longer authorized")
                if job["governance_id"]:
                    self.revalidate_job(job)
                return True

            kwargs = {"actor": trusted_actor}
            if "on_event" in inspect.signature(execute_plan).parameters:
                kwargs["on_event"] = checkpoint
            if "authorize" in inspect.signature(execute_plan).parameters:
                kwargs["authorize"] = authorize_access
            result = await execute_plan(job["plan"], data, **kwargs)
            if job["governance_id"]:
                self.revalidate_job(job)
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
                "plans": self.agreements(actor),
                "capabilities": {
                    "synthetic_rehearsal": True,
                    "signed_consent": True,
                    "confidential_submission": False,
                    "model_provider": "tinfoil",
                    "model": "deepseek-v4-1-flash",
                    "operator_prevalidation": True,
                    "live_model_verification": "required_at_execution",
                    "confidential_execution": False,
                    "authorization_checkpoints": "private runtime events; not every access",
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
            return {"plans": self.agreements(self.actor(request))}

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
            old = self.store.plan(actor, ident)
            governed = any(p["digest"] == old["digest"] for p in self.governance.plans(actor))

            def supersede():
                if governed and old["digest"] != contracts.plan_digest(plan):
                    # Register the new draft while the editor CAS lock is held. If
                    # another DB fails, governance advances first and fails closed.
                    roster_id = None
                    if plan["mode"] == "confidential":
                        roster_id = self.binding(actor, old["plan"]).get("roster_id")
                    self.governance.register_plan(plan, actor, roster_id=roster_id)

            return self.store.save_plan(
                actor,
                plan,
                contracts.plan_digest(plan),
                expected_digest=body["expected_digest"],
                before_commit=supersede,
            )

        @router.post("/check")
        async def check(request: Request):
            actor, body = self.actor(request), await self.body(request)
            try:
                plan = contracts.validate_plan(body["plan"])
                self.runtime_check(plan)
                for ident in plan["scope"]["dataset_ids"]:
                    EvidenceStore(self.dataset(actor, ident), plan["scope"])
                if not plan["scope"]["dataset_ids"]:
                    raise ValueError("Select an explicit input dataset")
            except (ValueError, RuntimeError):
                return {
                    "valid": False,
                    "diagnostics": ["Plan or runtime operator configuration unavailable."],
                    "requirements": {},
                }
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

        @router.post("/plans/{ident}/bind")
        async def bind_plan(request: Request, ident: str):
            actor = self.actor(request)
            package = self.store.plan(actor, ident)
            self.bind(actor, package["plan"])
            return {"bound": True, "digest": package["digest"]}

        @router.get("/plans/{ident}/readiness")
        async def readiness(request: Request, ident: str):
            actor = self.actor(request)
            package = self.accessible_plan(actor, ident)
            return self.governance.readiness(package["digest"], actor)

        @router.post("/plans/{ident}/reviews")
        async def review(request: Request, ident: str):
            actor, body, package = await saved(request, ident)
            # JSON pointer fixes the exact clause bytes as well as the whole plan.
            clause = body["clause"]
            if not isinstance(clause, str) or not clause.startswith("/"):
                raise ValueError("JSON pointer required")
            value = package["plan"]
            try:
                for token in clause[1:].split("/"):
                    token = token.replace("~1", "/").replace("~0", "~")
                    value = value[int(token)] if isinstance(value, list) else value[token]
            except (IndexError, KeyError, TypeError, ValueError):
                raise ValueError("Clause unavailable") from None
            recipients = body.get("recipients", [actor])
            owner = self.store.current_owner(package["digest"])
            if not isinstance(recipients, list) or not set(recipients) <= self.permitted(
                package["plan"]
            ) | {owner}:
                raise ValueError("Unauthorized note recipients")
            note = body["note"]
            if not isinstance(note, str) or not 0 < len(note) <= 16000:
                raise ValueError("Review note required")
            return self.store.annotate(
                "review",
                package["digest"],
                {
                    "actor": actor,
                    "plan_digest": package["digest"],
                    "clause": clause,
                    "clause_digest": commitment("aiab-workbench/clause/v1", value),
                    "note": note,
                    "recipients": sorted(set(recipients) | {actor}),
                    "verification": "authenticated_note_not_signing_consent",
                },
            )

        @router.get("/plans/{ident}/reviews")
        async def reviews(request: Request, ident: str):
            actor = self.actor(request)
            digest = request.query_params.get("digest")
            if digest:
                try:
                    self.store.revision(actor, ident, digest)
                except KeyError:
                    old = [
                        p
                        for p in self.governance.plans(actor)
                        if p["digest"] == digest and p["plan"]["id"] == ident
                    ]
                    if not old:
                        raise KeyError("Review version not found") from None
            else:
                digest = self.accessible_plan(actor, ident)["digest"]
            return {"reviews": self.store.annotations("review", digest, actor)}

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
            self.runtime_check(plan)
            if body["dataset_id"] not in plan["scope"]["dataset_ids"]:
                raise ValueError("Dataset outside agreement")
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
                    request_id=body.get("request_id") or secrets.token_hex(24),
                    authorization=grant["authorization"],
                )["run_id"]
            else:
                raise ValueError("Unknown run mode")
            run_id = self.store.enqueue(
                actor, plan, package["digest"], data["id"], mode, governance_id=governance_id
            )
            task = asyncio.create_task(self.execute(actor, run_id))
            self.tasks[run_id] = task
            # Also clean up cancellation before the coroutine starts/claims a job.
            task.add_done_callback(lambda _finished: self.tasks.pop(run_id, None))
            return {"run_id": run_id}

        @router.get("/runs/{ident}")
        async def result(request: Request, ident: str):
            actor = self.actor(request)
            header = self.run_header(actor, ident)
            if header["mode"] == "audit":
                # No private result/error/trace is ever serialized for any audit reader.
                package = (
                    self.release_for(actor, ident) if header["status"] == "completed" else None
                )
                out = {
                    "run_id": ident,
                    "status": header["status"],
                    "mode": "audit",
                    "plan_digest": header["digest"],
                    "error": None,
                }
                if package:
                    out.update(result=package["projection"], receipt=package["receipt"])
                return out
            job = self.store.job(actor, ident)
            return {
                "run_id": ident,
                "status": job["status"],
                "mode": "rehearse",
                "plan_digest": job["digest"],
                "error": job["error"],
                "result": job["result"],
                "verification": {"origin": "synthetic", "consented_audit": False},
            }

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

        @router.post("/runs/{ident}/release")
        async def release(request: Request, ident: str):
            actor = self.actor(request)
            return self.release_for(actor, ident)

        @router.get("/receipts/public-key")
        async def receipt_key():
            return {"algorithm": "Ed25519", "public_key": self.governance.receipt_public_key()}

        @router.post("/receipts/verify")
        async def verify_receipt(request: Request):
            body = await self.body(request)
            # The independently pinned caller key is mandatory; never trust embedded keys.
            if not isinstance(body.get("package"), dict) or not isinstance(
                body.get("public_key"), str
            ):
                raise ValueError("Receipt package and pinned key required")
            if body.get("expected") is not None and not isinstance(body["expected"], dict):
                raise ValueError("Expected receipt bindings must be an object")
            GovernanceStore.verify_receipt(
                body["package"], body["public_key"], expected=body.get("expected")
            )
            return {"verified": True, "meaning": "issuer_bytes_and_bindings_only"}

        @router.post("/runs/{ident}/challenge")
        async def challenge(request: Request, ident: str):
            actor, body = self.actor(request), await self.body(request)
            header = self.run_header(actor, ident, participant=True)
            synthetic = header["mode"] == "rehearse"
            if synthetic:
                job = self.store.job(actor, ident)
                findings = (job["result"] or {}).get("findings", [])
            else:
                # Previously disclosed findings remain challengeable after withdrawal
                # or cancellation. Reading that immutable package is not a new release.
                try:
                    status = self.governance.run_status(header["governance_id"], actor)
                except ValueError:
                    status = {"releases": []}
                package = (
                    status["releases"][0] if status["releases"] else self.release_for(actor, ident)
                )
                findings = package["projection"].get("findings", [])
            finding = body["finding_id"]
            if not isinstance(findings, list) or finding not in {f["id"] for f in findings}:
                raise ValueError("Finding unavailable")
            correction, refs = body["correction"], body["evidence_ids"]
            if not isinstance(correction, str) or not 0 < len(correction) <= 16000:
                raise ValueError("Correction required")
            if not isinstance(refs, list) or any(not isinstance(r, str) for r in refs):
                raise ValueError("Evidence references required")
            if refs:
                # No reference delivery path exists for recipients. Only owners can
                # attach scoped evidence, which stays private to the owner.
                if actor != header["owner"]:
                    raise ValueError("Evidence references unavailable")
                if not synthetic:
                    self.revalidate_job(header)
                EvidenceStore(
                    self.dataset(actor, header["plan"]["scope"]["dataset_ids"][0]),
                    header["plan"]["scope"],
                ).fetch(refs)
            recipients = body["recipients"]
            if (
                not isinstance(recipients, list)
                or not recipients
                or any(not isinstance(r, str) for r in recipients)
            ):
                raise ValueError("Explicit recipients required")
            if synthetic and set(recipients) != {actor}:
                raise ValueError("Synthetic challenges are owner-only")
            payload = {
                "finding_id": finding,
                "correction": correction,
                "evidence_ids": refs,
                "plan_digest": header["digest"],
            }
            proof = commitment("aiab-workbench/correction/v1", payload)
            if synthetic:
                if body.get("disposition") or body.get("parent_id"):
                    raise ValueError("Synthetic challenges have no governance disposition")
                record = {
                    "id": secrets.token_hex(16),
                    "actor": actor,
                    "recipients": [actor],
                    "claim_id": finding,
                    "requested_remedy": body["requested_remedy"],
                    "correction_commitment": proof,
                    "parent_id": None,
                    "disposition": None,
                }
            else:
                record = self.governance.challenge(
                    header["governance_id"],
                    actor,
                    claim_id=finding,
                    correction_commitment=proof,
                    requested_remedy=body["requested_remedy"],
                    recipients=recipients,
                    parent_id=body.get("parent_id"),
                    disposition=body.get("disposition"),
                    corrected_release_id=body.get("corrected_release_id"),
                )
            stored = self.store.annotate(
                "challenge",
                ident,
                dict(
                    record,
                    correction=correction,
                    evidence_ids=refs,
                    plan_digest=header["digest"],
                    verification="synthetic_unverified"
                    if synthetic
                    else "authenticated_challenge_not_factual_verification",
                ),
            )
            return stored

        @router.get("/runs/{ident}/challenges")
        async def challenges(request: Request, ident: str):
            actor = self.actor(request)
            header = self.run_header(actor, ident, participant=True)
            rows = self.store.annotations("challenge", ident, actor)
            if header["mode"] == "audit":
                permitted = {
                    v["id"]: v for v in self.governance.challenges(header["governance_id"], actor)
                }
                # The ledger is authoritative; an interrupted private-material write
                # still leaves the immutable challenge commitment visible.
                private = {v["id"]: v for v in rows}
                rows = [
                    dict(value, **{k: v for k, v in private.get(key, {}).items() if k not in value})
                    for key, value in permitted.items()
                ]
            if actor != header["owner"]:
                rows = [{k: v for k, v in row.items() if k != "evidence_ids"} for row in rows]
            return {"challenges": rows}

        @router.get("/history")
        async def history(request: Request):
            return {"history": self.governance.history(self.actor(request))}
