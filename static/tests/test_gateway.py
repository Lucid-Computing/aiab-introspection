"""The optional gateway must never release a credential before approval."""

from __future__ import annotations

import asyncio
import hashlib
import os
import ssl
import sys
import time
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from aiab.reporter import canonical_json
from auditor.attestation import AttestedClient, verify_attestation
from auditor.gateway import MAX_BODY, GatewayConfig, create_app, forward_verified, run_isolated
from auditor.verify import VerificationError
from tests.test_attestation import DIGEST, HOST, REPO, install_cli, install_secure
from tests.test_auditor import artifacts

TOKEN = "test-gateway-token-" + "X" * 32


@pytest.fixture
def gateway(monkeypatch):
    source = artifacts()
    manifest = {**source.manifest, "gateway_authentication": "bearer"}
    digest = hashlib.sha256(canonical_json(manifest).encode()).hexdigest()
    data = SimpleNamespace(identity={**source.identity, "policy_digest": digest}, manifest=manifest)
    install_cli(monkeypatch)
    document, _ = install_secure(monkeypatch, data)
    seen = []
    state = SimpleNamespace(
        status=200,
        payload=b'{"choices": []}',
        redirect=None,
        health_status=200,
        health={
            "status": "ready",
            "aiab_mode": "tinfoil",
            "enforcement_mode": manifest["enforcement_mode"],
        },
    )

    def handler(request):
        seen.append(request)
        if request.url.path == "/aiab/health":
            return httpx.Response(state.health_status, json=state.health)
        if request.url.path.startswith("/v1/"):
            return httpx.Response(
                state.status,
                content=state.payload,
                headers={"set-cookie": "private", "location": state.redirect or ""},
            )
        values = {
            "/aiab/pubkey": data.identity,
            "/aiab/manifest": data.manifest,
            "/aiab/reports": [],
        }
        return httpx.Response(
            200, json=values[request.url.path], headers={"set-cookie": "upstream_cookie=private"}
        )

    def client(evidence):
        result = AttestedClient(evidence)
        result._http.close()
        result._http = httpx.Client(transport=httpx.MockTransport(handler))
        return result

    monkeypatch.setattr("auditor.gateway.AttestedClient", client)

    async def inline(config, method, path, body):
        return forward_verified(config, method, path, body)

    monkeypatch.setattr("auditor.gateway.run_isolated", inline)
    config = GatewayConfig(HOST, REPO, DIGEST, digest, TOKEN)
    return SimpleNamespace(config=config, seen=seen, state=state, data=data, document=document)


async def call(config, path="/v1/chat/completions", method="POST", **kwargs):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(config)), base_url="http://gateway"
    ) as client:
        return await client.request(method, path, **kwargs)


async def test_approval_precedes_inference_and_browser_credentials_never_forward(gateway):
    response = await call(
        gateway.config,
        content=b'{"messages": []}',
        headers={
            "authorization": "Basic private-browser-credential",
            "cookie": "private-browser-cookie",
            "x-forwarded-host": "evil.example",
            "x-api-key": "private-browser-key",
        },
    )
    assert response.status_code == 200
    assert "set-cookie" not in response.headers and "location" not in response.headers
    assert [r.url.path for r in gateway.seen] == [
        "/aiab/pubkey",
        "/aiab/manifest",
        "/aiab/pubkey",
        "/v1/chat/completions",
    ]
    for request in gateway.seen[:-1]:
        assert "authorization" not in request.headers
    inference = gateway.seen[-1]
    assert inference.headers["authorization"] == "Bearer " + TOKEN
    assert "cookie" not in inference.headers
    assert "x-forwarded-host" not in inference.headers and "x-api-key" not in inference.headers
    assert inference.content == b'{"messages": []}'


@pytest.mark.parametrize("failure", ["release", "policy", "sdk", "auth_policy", "cli"])
async def test_verification_failure_never_sends_credentials_or_body(gateway, monkeypatch, failure):
    config = gateway.config
    if failure == "release":
        config = replace(config, release_digest="e" * 64)
    elif failure == "policy":
        config = replace(config, policy_digest="f" * 64)
    elif failure == "sdk":
        gateway.document.security_verified = False
    elif failure == "auth_policy":
        gateway.data.manifest["gateway_authentication"] = "none"
        digest = hashlib.sha256(canonical_json(gateway.data.manifest).encode()).hexdigest()
        gateway.data.identity["policy_digest"] = digest
        config = replace(config, policy_digest=digest)
    else:
        monkeypatch.setattr(
            "auditor.gateway.verify_attestation",
            lambda *args: (_ for _ in ()).throw(VerificationError("secret-diagnostic")),
        )
    response = await call(config, content=b"private-prompt")
    assert response.status_code == 503
    assert all("authorization" not in r.headers for r in gateway.seen)
    assert all(not r.url.path.startswith("/v1/") for r in gateway.seen)
    assert "private" not in response.text and "secret" not in response.text


@pytest.mark.parametrize("status", [301, 302, 307, 308, 400, 401, 403, 429, 500])
async def test_redirects_and_error_bodies_never_relay(gateway, status):
    gateway.state.status = status
    gateway.state.redirect = "https://attacker.example/steal"
    gateway.state.payload = TOKEN.encode()
    response = await call(gateway.config, content=b"{}")
    assert response.status_code == (403 if status == 403 else 503)
    assert TOKEN not in response.text and "location" not in response.headers
    assert all(r.url.host == HOST for r in gateway.seen)
    assert len([r for r in gateway.seen if r.url.path.startswith("/v1/")]) == 1


@pytest.mark.parametrize("payload", [TOKEN.encode(), b"x" * 2_097_153])
async def test_successful_but_secret_or_oversized_response_is_suppressed(gateway, payload):
    gateway.state.payload = payload
    response = await call(gateway.config, content=b"{}")
    assert response.status_code == 503 and TOKEN not in response.text


@pytest.mark.parametrize(
    "path", ["/v1/other", "/v1/models?url=https://evil.example", "/v1/%6dodels"]
)
async def test_destination_control_is_rejected_before_verification(gateway, path):
    response = await call(gateway.config, path=path, method="GET")
    assert response.status_code == 404 and not gateway.seen


async def test_oversized_request_rejected_before_verification(gateway):
    response = await call(gateway.config, content=b"x" * (MAX_BODY + 1))
    assert response.status_code == 413 and not gateway.seen


async def test_audit_and_health_routes_verify_without_bearer(gateway):
    for path in ["/aiab/pubkey", "/aiab/manifest", "/aiab/reports", "/aiab/health"]:
        response = await call(gateway.config, path=path, method="GET")
        assert response.status_code == 200
    assert all("authorization" not in r.headers for r in gateway.seen)


def test_sdk_tls_rotation_cannot_retry_with_secret(monkeypatch):
    from tinfoil.client import _ReVerifyingTransport

    install_cli(monkeypatch)
    install_secure(monkeypatch, artifacts())
    attempts = []

    def old_tls(request):
        attempts.append(request)
        raise ssl.SSLCertVerificationError("certificate verify failed")

    with AttestedClient(verify_attestation(HOST, REPO, DIGEST)) as client:
        client._http.close()
        # Exercise the real SDK retry wrapper against our FixedIdentityClient.
        transport = _ReVerifyingTransport(client._secure, httpx.MockTransport(old_tls))
        client._http = httpx.Client(transport=transport)
        with pytest.raises(VerificationError, match="inference request failed"):
            client.inference("POST", "/v1/chat/completions", b"private-prompt", TOKEN)
        assert len(attempts) == 1
        with pytest.raises(VerificationError, match="TLS identity changed"):
            client._secure._rebuild_sync_transport()


def test_missing_approvals_and_invalid_credential_fail_closed():
    for field in ["host", "repo", "release_digest", "policy_digest", "token"]:
        args = dict(host=HOST, repo=REPO, release_digest=DIGEST, policy_digest=DIGEST, token=TOKEN)
        args[field] = ""
        with pytest.raises(VerificationError):
            GatewayConfig(**args)
    with pytest.raises(VerificationError):
        GatewayConfig(HOST, REPO, "0" * 64, DIGEST, TOKEN)
    assert TOKEN not in repr(GatewayConfig(HOST, REPO, DIGEST, DIGEST, TOKEN))


@pytest.mark.parametrize("problem", ["status", "body", "mode"])
async def test_unhealthy_runtime_never_reported_ready(gateway, problem):
    if problem == "status":
        gateway.state.health_status = 503
    elif problem == "body":
        gateway.state.health["status"] = "unavailable"
    else:
        gateway.state.health["enforcement_mode"] = "different"
    response = await call(gateway.config, path="/aiab/health", method="GET")
    assert response.status_code == 503
    assert all("authorization" not in r.headers for r in gateway.seen)


async def test_latest_without_reports_has_compatible_404(gateway):
    response = await call(gateway.config, path="/aiab/reports/latest", method="GET")
    assert response.status_code == 404
    assert any(r.url.path == "/aiab/reports" for r in gateway.seen)


async def test_slow_body_has_deadline_before_any_verification(gateway, monkeypatch):
    monkeypatch.setattr("auditor.gateway.BODY_TIMEOUT_SECONDS", 0.01)

    async def body():
        yield b"first"
        await asyncio.sleep(1)
        yield b"last"

    response = await call(gateway.config, content=body())
    assert response.status_code == 503 and not gateway.seen


@pytest.mark.parametrize("unsafe", ["mode", "symlink", "oversized"])
def test_gateway_private_credential_file_is_strict(tmp_path, monkeypatch, unsafe):
    path = tmp_path / "credential"
    path.write_text(TOKEN if unsafe != "oversized" else "x" * 5000)
    path.chmod(0o400 if unsafe != "mode" else 0o644)
    if unsafe == "symlink":
        link = tmp_path / "link"
        link.symlink_to(path)
        path = link
    monkeypatch.setenv("AIAB_GATEWAY_TOKEN_FILE", str(path))
    with pytest.raises(VerificationError, match="configuration is missing or invalid"):
        GatewayConfig.from_env()


@pytest.mark.parametrize("cancel", [False, True])
async def test_stuck_worker_is_killed_and_same_app_slot_recovers(monkeypatch, cancel):
    config = GatewayConfig(HOST, REPO, DIGEST, DIGEST, TOKEN)
    real_spawn = asyncio.create_subprocess_exec
    processes = []
    ready = asyncio.Event()
    scripts = [
        "import sys,time; sys.stdin.buffer.read(); time.sleep(60)",
        "import sys,json; sys.stdin.buffer.read(); "
        "print(json.dumps(dict(status=200, body='e30=')))",
    ]

    async def spawn(*args, **kwargs):
        assert args == (sys.executable, "-m", "auditor.gateway_worker")
        assert TOKEN not in str(args) + str(kwargs)
        assert "ANTHROPIC_API_KEY" not in kwargs["env"]
        process = await real_spawn(sys.executable, "-c", scripts.pop(0), **kwargs)
        processes.append(process)
        ready.set()
        return process

    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-ambient-private-value")
    monkeypatch.setattr("auditor.gateway.asyncio.create_subprocess_exec", spawn)
    monkeypatch.setattr("auditor.gateway.WORKER_TIMEOUT_SECONDS", 0.15)
    app = create_app(config)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway"
    ) as client:
        start = time.monotonic()
        task = asyncio.create_task(client.post("/v1/chat/completions", content=b"private-prompt"))
        await ready.wait()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            response = await task
            assert response.status_code == 503 and "private" not in response.text
        assert time.monotonic() - start < 3
        assert processes[0].returncode is not None
        recovered = await client.post("/v1/chat/completions", content=b"{}")
        assert recovered.status_code == 200 and recovered.json() == {}


async def test_exited_worker_descendant_holding_pipe_is_killed(monkeypatch, tmp_path):
    config = GatewayConfig(HOST, REPO, DIGEST, DIGEST, TOKEN)
    pidfile = tmp_path / "child.pid"
    script = (
        "import os,subprocess,sys; sys.stdin.buffer.read(); "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        f"open({str(pidfile)!r},'w').write(str(child.pid)); os._exit(0)"
    )
    real_spawn = asyncio.create_subprocess_exec
    processes = []

    async def spawn(*args, **kwargs):
        process = await real_spawn(sys.executable, "-c", script, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr("auditor.gateway.asyncio.create_subprocess_exec", spawn)
    monkeypatch.setattr("auditor.gateway.WORKER_TIMEOUT_SECONDS", 0.25)
    response = await run_isolated(config, "POST", "/v1/chat/completions", b"private-prompt")
    assert response.status_code == 503
    assert processes[0].returncode == 0
    child = int(pidfile.read_text())
    # A killed orphan can briefly remain as a zombie until host init reaps it.
    for _ in range(50):
        try:
            with open(f"/proc/{child}/stat") as stream:
                if stream.read().split()[2] == "Z":
                    break
        except FileNotFoundError:
            break
        await asyncio.sleep(0.01)
    else:
        os.kill(child, 9)
        pytest.fail("Timed-out verifier descendant survived cleanup")
