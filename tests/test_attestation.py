"""Production audit transport must bind every artifact to an approved release."""

from __future__ import annotations

import copy
import io
import json
import subprocess
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from aiab.config import Config, ConfigurationError
from auditor.attestation import AttestedClient, production_inputs, verify_attestation
from auditor.cli import main
from auditor.policy import expected_manifest
from auditor.verify import VerificationError
from tests.test_auditor import artifacts
from tests.test_core import SENTINEL, appliance, request
from tests.test_tinfoil_classifier import FakeSDK

HOST = "aiab.example.com"
REPO = "example/aiab"
DIGEST = "a" * 64
KEY = "b" * 64
MEASUREMENT = {"type": "https://tinfoil.sh/predicate/sev-snp-guest/v2", "registers": ["c" * 96]}


def cli_record() -> dict[str, Any]:
    return {
        "timestamp": "2026-09-17T00:00:00Z",
        "enclave": HOST,
        "repo": REPO,
        "digest": DIGEST,
        "measurements": {
            "sigstore": copy.deepcopy(MEASUREMENT),
            "enclave": copy.deepcopy(MEASUREMENT),
        },
        "keys": {"enclave": KEY, "connection": KEY},
        "status": "ok",
    }


def install_cli(monkeypatch: pytest.MonkeyPatch, payload: Any = None, returncode: int = 0) -> None:
    def run(argv: list[str], **kwargs: Any) -> Any:
        assert argv == ["tinfoil", "attestation", "verify", "-e", HOST, "-r", REPO, "-j"]
        assert kwargs == {
            "stdin": subprocess.DEVNULL,
            "capture_output": True,
            "timeout": 60,
            "check": False,
        }
        return SimpleNamespace(
            returncode=returncode,
            stdout=json.dumps(cli_record() if payload is None else payload).encode(),
            stderr=b"private-diagnostic-sentinel",
        )

    monkeypatch.setattr("auditor.attestation.subprocess.run", run)


def install_secure(monkeypatch: pytest.MonkeyPatch, data: Any) -> tuple[Any, list[str]]:
    from tinfoil.attestation.types import Measurement, PredicateType
    from tinfoil.client import VerificationDocument, VerificationStepState

    document = VerificationDocument(
        config_repo=REPO,
        enclave_host=HOST,
        release_digest=DIGEST,
        code_measurement=Measurement(PredicateType(MEASUREMENT["type"]), MEASUREMENT["registers"]),
        tls_public_key=KEY,
        security_verified=True,
        steps={
            name: VerificationStepState("success")
            for name in ("fetch_digest", "verify_code", "verify_enclave", "compare_measurements")
        },
    )
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == HOST and request.url.scheme == "https"
        paths.append(request.url.path)
        payloads = {
            "/aiab/pubkey": data.identity,
            "/aiab/manifest": data.manifest,
            "/aiab/reports": data.sign(),
        }
        return httpx.Response(200, json=payloads[request.url.path])

    class FakeSecure:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs.keys() == {"enclave", "repo", "transport", "user_cache_secret"}
            assert kwargs["enclave"] == HOST and kwargs["repo"] == REPO
            assert kwargs["transport"] == "tls" and len(kwargs["user_cache_secret"]) == 64

        def make_secure_http_client(self) -> httpx.Client:
            return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)

        def get_verification_document(self) -> Any:
            return document

    monkeypatch.setattr("tinfoil.SecureClient", FakeSecure)
    return document, paths


def test_exact_cli_flags_and_verified_release_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    install_cli(monkeypatch)
    evidence = verify_attestation(HOST, REPO, DIGEST)
    assert evidence.release_digest == DIGEST
    assert evidence.tls_public_key == KEY and evidence.code_measurement == MEASUREMENT


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("status", "enclave_only"),
        ("status", "fail"),
        ("debug", True),
        ("debug", False),
        ("enclave", "other.example.com"),
        ("repo", "other/repo"),
        ("digest", "d" * 64),
        ("error", "private-diagnostic-sentinel"),
        ("measurements", {}),
        ("keys", {"enclave": KEY, "connection": "d" * 64}),
        ("timestamp", 1),
    ],
)
def test_incomplete_debug_mismatched_or_unverified_record_rejected(
    monkeypatch: pytest.MonkeyPatch, key: str, value: Any
) -> None:
    install_cli(monkeypatch, {**cli_record(), key: value})
    with pytest.raises(VerificationError) as caught:
        verify_attestation(HOST, REPO, DIGEST)
    assert "private-diagnostic-sentinel" not in str(caught.value)


def test_measurements_must_match_and_have_complete_known_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for measurement in (
        {**MEASUREMENT, "registers": ["d" * 96]},
        {**MEASUREMENT, "registers": []},
        {**MEASUREMENT, "type": "debug"},
    ):
        record = cli_record()
        record["measurements"]["enclave"] = measurement
        install_cli(monkeypatch, record)
        with pytest.raises(VerificationError):
            verify_attestation(HOST, REPO, DIGEST)


def test_command_errors_and_invalid_json_never_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    install_cli(monkeypatch, returncode=1)
    with pytest.raises(VerificationError, match="rejected"):
        verify_attestation(HOST, REPO, DIGEST)
    for error in (FileNotFoundError(), subprocess.TimeoutExpired("tinfoil", 60)):

        def fail(*_: Any, error: Exception = error, **__: Any) -> None:
            raise error

        monkeypatch.setattr("auditor.attestation.subprocess.run", fail)
        with pytest.raises(VerificationError, match="unavailable or timed out"):
            verify_attestation(HOST, REPO, DIGEST)
    monkeypatch.setattr(
        "auditor.attestation.subprocess.run",
        lambda *_, **__: SimpleNamespace(returncode=0, stdout=b"not JSON"),
    )
    with pytest.raises(VerificationError, match="Invalid JSON"):
        verify_attestation(HOST, REPO, DIGEST)


@pytest.mark.parametrize("command", ["verify", "report", "rollup"])
def test_every_production_command_requires_approvals(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    monkeypatch.delenv("EXPECTED_AIAB_DIGEST", raising=False)
    monkeypatch.delenv("EXPECTED_POLICY_DIGEST", raising=False)
    for flags in ([], ["--repo", REPO], ["--repo", REPO, "--expected-digest", DIGEST]):
        with pytest.raises(SystemExit):
            main([command, "--host", HOST, *flags])
        assert "independently approved" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["verify", "report", "rollup"])
def test_production_commands_fetch_all_artifacts_over_verified_sdk(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    data = artifacts()
    install_cli(monkeypatch)
    _, paths = install_secure(monkeypatch, data)
    monkeypatch.setenv("EXPECTED_AIAB_DIGEST", DIGEST)
    monkeypatch.setenv("EXPECTED_POLICY_DIGEST", data.identity["policy_digest"])
    main([command, "--host", HOST, "--repo", REPO])
    output = capsys.readouterr()
    assert f"Tinfoil release digest: {DIGEST}" in output.out
    assert "INSECURE" not in output.err + output.out
    assert paths[0] == paths[-1] == "/aiab/pubkey"
    assert ("/aiab/reports" in paths) == (command != "verify")
    if command == "rollup":
        assert "Totals (2 unique periods" in output.out


@pytest.mark.parametrize(
    "field", ["release_digest", "tls_public_key", "config_repo", "security_verified"]
)
def test_sdk_verification_must_match_cli_and_remain_stable(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    install_cli(monkeypatch)
    document, paths = install_secure(monkeypatch, artifacts())
    evidence = verify_attestation(HOST, REPO, DIGEST)
    with AttestedClient(evidence) as client:
        setattr(document, field, False if field == "security_verified" else "different")
        with pytest.raises(VerificationError):
            client.get("/aiab/pubkey")
        assert paths == []
    with pytest.raises(VerificationError, match="could not be verified"):
        AttestedClient(evidence)


def test_sdk_release_rotation_after_request_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    install_cli(monkeypatch)
    document, _ = install_secure(monkeypatch, artifacts())
    with AttestedClient(verify_attestation(HOST, REPO, DIGEST)) as client:
        original = client._http.get

        def get(*args: Any, **kwargs: Any) -> httpx.Response:
            response = original(*args, **kwargs)
            document.release_digest = "d" * 64
            return response

        monkeypatch.setattr(client._http, "get", get)
        with pytest.raises(VerificationError):
            client.get("/aiab/pubkey")


@pytest.mark.parametrize(
    "host", ["--help", "https://aiab.example.com", "host:443", "127.0.0.1", "a.b/path", "a.b\n"]
)
def test_production_destination_is_strict(host: str) -> None:
    with pytest.raises(VerificationError):
        production_inputs(host, REPO, DIGEST, DIGEST)


def test_policy_generation_is_offline_ignores_ambient_key_and_rejects_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TINFOIL_API_KEY", "private-sentinel")
    manifest = expected_manifest({"AIAB_MODE": "tinfoil"})
    assert manifest["classifier_implementation"] == "tinfoil"
    assert "private-sentinel" not in json.dumps(manifest)
    changed = expected_manifest({"AIAB_MODE": "tinfoil", "BLOCK_THRESHOLD": "0.9"})
    assert changed != manifest
    with pytest.raises(VerificationError):
        expected_manifest({"AIAB_MODE": "tinfoil", "TINFOIL_API_KEY": "private-sentinel"})


def test_tinfoil_mode_serves_plain_http_behind_shim(monkeypatch: pytest.MonkeyPatch) -> None:
    from aiab.main import main as serve

    config = Config.from_env({"AIAB_MODE": "tinfoil", "TINFOIL_API_KEY": "test-key"})
    assert config.listen_port == 8080
    assert Config.from_env({}).listen_port == 8443
    with pytest.raises(ConfigurationError):
        Config.from_env({"AIAB_MODE": "tinfoil"})
    monkeypatch.setattr("aiab.main.Config.from_env", lambda: config)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("aiab.main.uvicorn.run", lambda app, **kw: calls.append(kw))
    serve()
    assert calls[0]["port"] == 8080 and "ssl_keyfile" not in calls[0]
    assert calls[0]["access_log"] is False


async def test_tinfoil_runtime_verifies_real_sdk_and_forbids_mock(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from aiab.classifier import MockClassifier
    from aiab.proxy import create_app

    fake = FakeSDK()
    monkeypatch.setattr("tinfoil.TinfoilAI", fake.factory)
    config = Config.from_env(
        {"AIAB_MODE": "tinfoil", "TINFOIL_API_KEY": "test-key", "ENFORCEMENT_MODE": "block"}
    )
    with pytest.raises(ConfigurationError, match="SDK classifier required"):
        create_app(config, classifier=MockClassifier())
    async with appliance(config) as (client, runtime, forwarded):
        assert runtime.healthy
        response = await client.post("/v1/chat/completions", json=request(SENTINEL))
        assert response.status_code == 403 and not forwarded
    assert fake.options and fake.calls
    assert SENTINEL not in caplog.text


async def test_tinfoil_startup_verification_failure_aborts_lifespan_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from aiab.proxy import create_app

    fake = FakeSDK()
    fake.document.security_verified = False
    closed: list[bool] = []
    fake.client.close = lambda: closed.append(True)
    monkeypatch.setattr("tinfoil.TinfoilAI", fake.factory)
    config = Config.from_env({"AIAB_MODE": "tinfoil", "TINFOIL_API_KEY": "test-key"})
    app = create_app(config)
    with pytest.raises(RuntimeError, match="Classifier startup verification failed"):
        async with app.router.lifespan_context(app):
            pytest.fail("Failed Tinfoil startup must never yield a running application")
    assert not app.state.runtime.healthy
    assert closed and app.state.runtime.client.is_closed
    assert SENTINEL not in caplog.text


@pytest.mark.parametrize(
    ("status", "body", "success"),
    [
        (200, b'{"status":"ready"}', True),
        (503, b"{}", False),
        (200, b'{"status":"unavailable"}', False),
        (200, b"not JSON", False),
    ],
)
def test_container_healthcheck_requires_ready_loopback_without_environment_proxy(
    monkeypatch: pytest.MonkeyPatch, status: int, body: bytes, success: bool
) -> None:
    from aiab.healthcheck import main as healthcheck

    response = io.BytesIO(body)
    response.status = status

    def open_local(url: str, *, timeout: int) -> Any:
        assert url == "http://127.0.0.1:8080/aiab/health" and timeout == 3
        return response

    def build(proxy_handler: Any) -> Any:
        assert proxy_handler.proxies == {}
        return SimpleNamespace(open=open_local)

    monkeypatch.setenv("LISTEN_PORT", "8080")
    monkeypatch.setenv("HTTP_PROXY", "http://not-contacted.example")
    monkeypatch.setattr("aiab.healthcheck.urllib.request.build_opener", build)
    if success:
        healthcheck()
    else:
        with pytest.raises(SystemExit) as error:
            healthcheck()
        assert error.value.code == 1
