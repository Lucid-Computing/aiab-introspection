"""Authenticated audit API with bounded bodies and no exception reflection."""

from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from aiab.audit.protocol import AuditError, decode
from aiab.audit.runtime import AuditRuntime


def create_app(runtime: AuditRuntime, tokens: dict[str, str]) -> FastAPI:
    if set(tokens) != {"lab", "auditor", "logger"} or any(len(v) < 32 for v in tokens.values()):
        raise ValueError("Three role credentials of at least 32 characters required")
    if len(set(tokens.values())) != 3:
        raise ValueError("Role credentials must be distinct")

    @asynccontextmanager
    async def lifespan(app):
        yield
        await runtime.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.router.redirect_slashes = False
    admissions = asyncio.Semaphore(8)

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        try:
            if request.url.path not in {"/aiab/health", "/aiab/manifest", "/aiab/pubkey"}:
                headers = request.headers.getlist("authorization")
                supplied = headers[0].encode() if len(headers) == 1 else b""
                role = next(
                    (
                        role
                        for role, token in tokens.items()
                        if secrets.compare_digest(supplied, f"Bearer {token}".encode())
                    ),
                    None,
                )
                if role is None:
                    raise AuditError("unauthorized", 401)
                request.state.role = role
            if request.method == "POST":
                if admissions.locked():
                    raise AuditError("capacity_exceeded", 429)
                async with admissions:
                    async with asyncio.timeout(15):
                        response = await call_next(request)
            else:
                response = await call_next(request)
        except AuditError as exc:
            response = JSONResponse({"error": exc.code}, status_code=exc.status)
        except Exception:
            response = JSONResponse({"error": "internal_error"}, status_code=500)
        response.headers["cache-control"] = "no-store"
        return response

    @app.exception_handler(AuditError)
    async def audit_error(request, exc):
        return JSONResponse({"error": exc.code}, status_code=exc.status)

    async def body(request):
        if request.headers.get("content-encoding", "identity") != "identity":
            raise AuditError("unsupported_encoding", 415)
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise AuditError("unsupported_content_type", 415)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 2_097_152:
                raise AuditError("input_too_large", 413)
        return decode(bytes(raw))

    @app.post("/aiab/plans")
    async def plans(request: Request):
        if request.state.role not in {"lab", "auditor"}:
            raise AuditError("forbidden", 403)
        accepted, created = runtime.registry.register(await body(request))
        return JSONResponse(
            {"plan_digest": accepted.digest, "status": "accepted"},
            status_code=201 if created else 200,
        )

    @app.post("/aiab/runs")
    async def runs(request: Request):
        if request.state.role not in {"logger", "lab"}:
            raise AuditError("forbidden", 403)
        run = runtime.submit(await body(request), request.state.role)
        return JSONResponse(
            {
                "run_id": run.run_id,
                "plan_digest": run.plan_digest,
                "boot_id": runtime.boot_id,
                "status": "running" if run.envelope is None else "finished",
            },
            status_code=202,
        )

    @app.get("/aiab/runs/{run_id}/report")
    async def report(run_id: str, request: Request):
        if request.state.role not in {"lab", "auditor"}:
            raise AuditError("forbidden", 403)
        run = runtime.runs.get(run_id)
        if run is None:
            raise AuditError("unknown_run", 404)
        if run.envelope is None:
            return JSONResponse({"run_id": run_id, "status": "running"}, status_code=202)
        return Response(run.envelope, media_type="application/json")

    @app.get("/aiab/manifest")
    async def manifest():
        return runtime.manifest

    @app.get("/aiab/pubkey")
    async def pubkey():
        return runtime.identity

    @app.get("/aiab/health")
    async def health():
        return JSONResponse(
            {"status": "ready" if runtime.healthy else "unavailable", "boot_id": runtime.boot_id},
            status_code=200 if runtime.healthy else 503,
        )

    return app
