"""Optional host gateway: independently approve identity and policy before inference.

This is a serial, buffered demo gateway. It deliberately has no streaming, redirect,
URL-selection, credential passthrough, or unverified fallback mode.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass, field

import uvicorn
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response

from aiab.config import read_private_key
from auditor.attestation import AttestedClient, production_inputs, verify_attestation
from auditor.cli import fetch_artifacts
from auditor.verify import VerificationError, strict_json, verify_reports

MAX_BODY = 1_048_576
BODY_TIMEOUT_SECONDS = 10
WORKER_TIMEOUT_SECONDS = 30
AUDIT_PATHS = {"/aiab/pubkey", "/aiab/manifest", "/aiab/reports", "/aiab/reports/latest"}


@dataclass(frozen=True)
class GatewayConfig:
    host: str
    repo: str
    release_digest: str
    policy_digest: str
    token: str = field(repr=False)

    def __post_init__(self) -> None:
        production_inputs(self.host, self.repo, self.release_digest, self.policy_digest)
        if self.release_digest == "0" * 64 or self.policy_digest == "0" * 64:
            raise VerificationError("Placeholder approvals are forbidden")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", self.token):
            raise VerificationError("Invalid gateway credential")

    @classmethod
    def from_env(cls) -> GatewayConfig:
        # Only this gateway credential is loaded. Provider credentials stay in
        # the enclave. Public approval values never come from the live endpoint.
        try:
            token = read_private_key(
                os.environ["AIAB_GATEWAY_TOKEN_FILE"], "AIAB_GATEWAY_TOKEN_FILE"
            )
            return cls(
                os.environ["AIAB_ENCLAVE_HOST"],
                os.environ["AIAB_ENCLAVE_REPO"],
                os.environ["EXPECTED_AIAB_DIGEST"],
                os.environ["EXPECTED_POLICY_DIGEST"],
                token,
            )
        except (OSError, KeyError, UnicodeError, ValueError):
            raise VerificationError("Gateway configuration is missing or invalid") from None


def unavailable() -> JSONResponse:
    return JSONResponse({"error": "Verified enclave unavailable"}, status_code=503)


def forward_verified(config: GatewayConfig, method: str, path: str, body: bytes) -> Response:
    try:
        evidence = verify_attestation(config.host, config.repo, config.release_digest)
        with AttestedClient(evidence) as client:
            identity, manifest, reports = fetch_artifacts(
                client, reports=path in {"/aiab/reports", "/aiab/reports/latest"}
            )
            verify_reports(identity, manifest, reports, expected_policy_digest=config.policy_digest)
            if manifest.get("gateway_authentication") != "bearer":
                raise VerificationError("Approved enclave does not require gateway authentication")
            if path == "/aiab/health":
                response = client.get("/aiab/health")
                health = strict_json(response.content)
                if (
                    response.status_code != 200
                    or not isinstance(health, dict)
                    or health.get("status") != "ready"
                    or health.get("enforcement_mode") != manifest["enforcement_mode"]
                    or health.get("aiab_mode") != "tinfoil"
                ):
                    raise VerificationError("Enclave runtime is not ready")
                return JSONResponse(
                    {
                        "status": "ready",
                        "transport": "attested",
                        "enforcement_mode": health["enforcement_mode"],
                        "aiab_mode": "tinfoil",
                    }
                )
            if path == "/aiab/reports/latest":
                if not reports:
                    return JSONResponse(
                        {"error": "No finalized reporting period yet"}, status_code=404
                    )
                return JSONResponse(reports[-1])
            if path in AUDIT_PATHS:
                # These artifacts were fetched and checked on the same pinned
                # transport. Do not refetch after verification.
                value = {
                    "/aiab/pubkey": identity,
                    "/aiab/manifest": manifest,
                    "/aiab/reports": reports,
                }[path]
                return JSONResponse(value)
            upstream = client.inference(method, path, body, config.token)
            if upstream.status_code != 200:
                if upstream.status_code == 403:
                    return JSONResponse(
                        {
                            "error": {
                                "code": "aiab_blocked",
                                "type": "policy_violation",
                                "message": "Request blocked by AIAB policy",
                                "classifier_id": manifest["classifier_id"],
                            }
                        },
                        status_code=403,
                    )
                return unavailable()
            if config.token.encode() in upstream.content:
                raise VerificationError("Credential in upstream response")
            return Response(upstream.content, media_type="application/json")
    except Exception:
        # No exception text, upstream headers, or redirects cross this boundary.
        return unavailable()


async def run_isolated(config: GatewayConfig, method: str, path: str, body: bytes) -> Response:
    """Bound the entire verifier/SDK/inference operation, including child CLI.

    Secrets and bodies use anonymous stdin pipes, never argv, environment or disk.
    A new process group allows timeout to terminate a stuck CLI as well as SDK.
    """
    payload = json.dumps(
        {
            "config": asdict(config),
            "method": method,
            "path": path,
            "body": base64.b64encode(body).decode("ascii"),
        }
    ).encode()
    env = {
        key: os.environ[key]
        for key in ("PATH", "XDG_CACHE_HOME", "XDG_DATA_HOME", "PYTHONDONTWRITEBYTECODE")
        if key in os.environ
    }
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "auditor.gateway_worker",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )

        async def exchange() -> bytes:
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(payload)
            await process.stdin.drain()
            process.stdin.close()
            output = bytearray()
            while chunk := await process.stdout.read(65536):
                output.extend(chunk)
                if len(output) > 3_000_000:
                    raise VerificationError("Oversized worker response")
            await process.wait()
            return bytes(output)

        output = await asyncio.wait_for(exchange(), timeout=WORKER_TIMEOUT_SECONDS)
        if process.returncode != 0 or len(output) > 3_000_000:
            return unavailable()
        result = strict_json(output)
        content = base64.b64decode(result["body"], validate=True)
        if len(content) > 2_097_152 or config.token.encode() in content:
            return unavailable()
        if result["status"] not in {200, 403, 404, 503}:
            return unavailable()
        return Response(content, status_code=result["status"], media_type="application/json")
    except Exception:
        return unavailable()
    finally:
        if process is not None:
            # Kill the whole session even if the worker already exited, so a
            # spawned verifier cannot outlive a cancelled/failed request.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            # SIGKILL normally reaps immediately. Do not let an uninterruptible
            # kernel wait consume the browser budget; retain a background reaper
            # if the kernel cannot finish cleanup within the remaining margin.
            reaper = asyncio.create_task(process.wait())
            try:
                await asyncio.wait_for(asyncio.shield(reaper), timeout=2)
            except TimeoutError:
                pass


def create_app(config: GatewayConfig) -> FastAPI:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None, redirect_slashes=False)
    active = threading.Lock()

    @app.api_route(
        "/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]
    )
    async def proxy(request: Request, path: str) -> Response:
        path = "/" + path
        allowed = (
            request.method == "GET" and path in AUDIT_PATHS | {"/aiab/health", "/v1/models"}
        ) or (request.method == "POST" and path == "/v1/chat/completions")
        # Reject encoded aliases and query strings. Destination paths are literals.
        if not allowed or request.scope["raw_path"] != path.encode() or request.url.query:
            return JSONResponse({"error": "Unsupported route"}, status_code=404)
        if not active.acquire(blocking=False):
            return unavailable()
        try:
            body = bytearray()
            async with asyncio.timeout(BODY_TIMEOUT_SECONDS):
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > MAX_BODY:
                        return JSONResponse({"error": "Request too large"}, status_code=413)
            if request.method == "GET" and body:
                return JSONResponse({"error": "GET body unsupported"}, status_code=400)
            # Request headers are deliberately not passed to the transport.
            return await run_isolated(config, request.method, path, bytes(body))
        except Exception:
            return unavailable()
        finally:
            active.release()

    return app


def main() -> None:
    # SDK/HTTP diagnostics must not write prompt text, credentials, or URLs.
    logging.disable(logging.CRITICAL)
    try:
        app = create_app(GatewayConfig.from_env())
    except VerificationError:
        raise SystemExit("Gateway configuration is missing or invalid") from None
    # Container networking publishes this port exclusively on host loopback.
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8444,
        access_log=False,
        log_config=None,
        server_header=False,
        proxy_headers=False,
        limit_concurrency=4,
    )


if __name__ == "__main__":
    main()
