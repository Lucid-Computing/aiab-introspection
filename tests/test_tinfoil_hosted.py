"""File-only hosted classifier activation; synthetic credentials and offline transports."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import tinfoil

from aiab.classifier import MockClassifier, TinfoilLLMClassifier, make_classifier
from aiab.config import ANTHROPIC_MODEL, TINFOIL_MODEL, Config, ConfigurationError
from aiab.proxy import create_app
from aiab.reporter import build_manifest
from tests.test_anthropic import KEY as ANTHROPIC_KEY
from tests.test_anthropic import request, success
from tests.test_core import appliance, finalized
from tests.test_tinfoil_classifier import FakeSDK, answer

KEY = "synthetic-tinfoil-key-never-valid-at-provider"
ROOT = Path(__file__).resolve().parents[1]


def key_file(tmp_path: Path, content: bytes = KEY.encode(), mode: int = 0o400) -> Path:
    path = tmp_path / "tinfoil.secret"
    path.write_bytes(content)
    path.chmod(mode)
    return path


def hosted_settings(tmp_path: Path) -> Config:
    tinfoil_path = key_file(tmp_path)
    anthropic_path = tmp_path / "anthropic.secret"
    anthropic_path.write_text(ANTHROPIC_KEY)
    anthropic_path.chmod(0o400)
    return Config.from_env(
        {
            "AIAB_MODE": "local",
            "ENFORCEMENT_MODE": "block",
            "INFERENCE_PROVIDER": "anthropic",
            "ANTHROPIC_API_KEY_FILE": str(anthropic_path),
            "TINFOIL_API_KEY_FILE": str(tinfoil_path),
            "CLASSIFIER_MODEL": TINFOIL_MODEL,
        }
    )


@pytest.mark.parametrize("mode", [0o400, 0o600])
@pytest.mark.parametrize("ending", [b"", b"\n"])
def test_file_credential_is_frozen_private_and_selects_sdk(
    tmp_path: Path, mode: int, ending: bytes
) -> None:
    path = key_file(tmp_path, KEY.encode() + ending, mode)
    settings = Config.from_env({"TINFOIL_API_KEY_FILE": str(path)})
    path.unlink()
    assert settings.tinfoil_api_key == KEY
    assert settings.classifier_model == TINFOIL_MODEL
    assert settings.aiab_mode == "local" and settings.listen_port == 8443
    classifier = make_classifier(settings)
    assert isinstance(classifier, TinfoilLLMClassifier)
    assert KEY not in repr(settings) + repr(classifier)
    manifest = json.dumps(build_manifest(settings, classifier))
    assert KEY not in manifest and str(path) not in manifest


@pytest.mark.parametrize("legacy", ["", KEY])
def test_even_empty_legacy_variable_conflicts_with_file_source(tmp_path: Path, legacy: str) -> None:
    path = key_file(tmp_path)
    with pytest.raises(ConfigurationError, match="only one Tinfoil credential source") as error:
        Config.from_env({"TINFOIL_API_KEY_FILE": str(path), "TINFOIL_API_KEY": legacy})
    assert KEY not in str(error.value) and str(path) not in str(error.value)


@pytest.mark.parametrize(
    "content",
    [b"", b"\n", b"x\n\n", b"x\r\n", b"a b", b"a\x00b", b"a\tb", b"\xff", b"x" * 4098],
)
def test_invalid_file_contents_abort_config_without_disclosure(
    tmp_path: Path, content: bytes
) -> None:
    path = key_file(tmp_path, content)
    with pytest.raises(ConfigurationError, match="TINFOIL_API_KEY_FILE") as error:
        Config.from_env({"TINFOIL_API_KEY_FILE": str(path)})
    assert KEY not in str(error.value) and str(path) not in str(error.value)


@pytest.mark.parametrize("mode", [0o000, 0o200, 0o440, 0o640, 0o644, 0o700, 0o1400, 0o2400, 0o4400])
def test_unsafe_file_modes_fail_closed(tmp_path: Path, mode: int) -> None:
    path = key_file(tmp_path, mode=mode)
    with pytest.raises(ConfigurationError):
        Config.from_env({"TINFOIL_API_KEY_FILE": str(path)})


def test_all_symlink_components_and_non_regular_files_are_rejected(tmp_path: Path) -> None:
    target = key_file(tmp_path)
    link = tmp_path / "linked.secret"
    link.symlink_to(target)
    directory_link = tmp_path / "linked-directory"
    directory_link.symlink_to(tmp_path, target_is_directory=True)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    for filename in (
        "",
        "relative.secret",
        str(tmp_path / "missing"),
        str(tmp_path),
        str(fifo),
        str(link),
        str(directory_link / target.name),
        str(tmp_path / ".." / tmp_path.name / target.name),
    ):
        with pytest.raises(ConfigurationError, match="TINFOIL_API_KEY_FILE") as error:
            Config.from_env({"TINFOIL_API_KEY_FILE": filename})
        assert KEY not in str(error.value) and str(tmp_path) not in str(error.value)


@pytest.mark.parametrize("owner", [0, 12345, 54321])
def test_secret_owner_must_be_root_or_effective_uid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner: int
) -> None:
    path = key_file(tmp_path)
    metadata = path.stat()
    monkeypatch.setattr(os, "geteuid", lambda: 12345)
    monkeypatch.setattr(
        os,
        "fstat",
        lambda _: SimpleNamespace(st_mode=metadata.st_mode, st_uid=owner, st_size=metadata.st_size),
    )
    if owner in {0, 12345}:
        assert Config.from_env({"TINFOIL_API_KEY_FILE": str(path)}).tinfoil_api_key == KEY
    else:
        with pytest.raises(ConfigurationError):
            Config.from_env({"TINFOIL_API_KEY_FILE": str(path)})


def test_explicit_provider_never_falls_back_to_mock_and_legacy_remains_supported(
    tmp_path: Path,
) -> None:
    path = key_file(tmp_path)
    for environment in (
        {"CLASSIFIER_MODEL": TINFOIL_MODEL},
        {"TINFOIL_API_KEY": "", "CLASSIFIER_MODEL": TINFOIL_MODEL},
        {"AIAB_MODE": "tinfoil", "TINFOIL_API_KEY_FILE": ""},
        {"TINFOIL_API_KEY_FILE": str(path), "CLASSIFIER_MODEL": "mock-keywords/v1"},
    ):
        with pytest.raises(ConfigurationError):
            Config.from_env(environment)
    assert isinstance(make_classifier(Config.from_env({"TINFOIL_API_KEY": ""})), MockClassifier)
    legacy = Config.from_env({"TINFOIL_API_KEY": KEY})
    assert isinstance(make_classifier(legacy), TinfoilLLMClassifier)
    with pytest.raises(ConfigurationError):
        make_classifier(replace(Config.from_env({}), classifier_model=TINFOIL_MODEL))
    with pytest.raises(ConfigurationError, match="SDK classifier required"):
        create_app(
            Config.from_env({"TINFOIL_API_KEY_FILE": str(path)}), classifier=MockClassifier()
        )


async def test_hosted_tinfoil_allows_only_classified_messages_to_anthropic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    sdk = FakeSDK()
    monkeypatch.setattr(tinfoil, "TinfoilAI", sdk.factory)
    settings = hosted_settings(tmp_path)
    sdk.outcomes = [
        answer('{"flagged":false,"confidence":0}'),
        answer('{"flagged":true,"confidence":0.95}'),
        RuntimeError(KEY),
        RuntimeError(KEY),
    ]

    async def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=success())

    async with appliance(settings, handler=upstream) as (client, runtime, forwarded):
        assert runtime.healthy and isinstance(runtime.classifier, TinfoilLLMClassifier)
        assert sdk.options[0]["api_key"] == KEY
        assert sdk.options[0]["transport"] == "tls"
        for status in (200, 403, 503):
            response = await client.post("/v1/chat/completions", json=request())
            assert response.status_code == status
        assert len(forwarded) == 1 and len(sdk.calls) == 4
        assert all(call["model"] == TINFOIL_MODEL for call in sdk.calls)
        assert forwarded[0].headers["x-api-key"] == ANTHROPIC_KEY
        assert KEY not in str(forwarded[0].headers) + forwarded[0].content.decode()
        report = finalized(runtime)
        assert report["requests_forwarded"] == report["requests_blocked"] == 1
        assert report["classifier_errors"] == 1
        for endpoint in (
            "/v1/models",
            "/aiab/health",
            "/aiab/manifest",
            "/aiab/pubkey",
            "/aiab/reports",
        ):
            public = await client.get(endpoint)
            assert KEY not in public.text and ANTHROPIC_KEY not in public.text
            assert str(tmp_path) not in public.text
        health = (await client.get("/aiab/health")).json()
        assert health["classifier_verification"]["security_verified"] is True
        assert runtime.config.aiab_mode == "local"
    assert KEY not in caplog.text and ANTHROPIC_KEY not in caplog.text


@pytest.mark.parametrize("failure", ["verification", "constructor"])
async def test_hosted_startup_verification_failure_keeps_all_admission_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    sdk = FakeSDK()
    sdk.document.security_verified = False

    def failing_constructor(**_: Any) -> None:
        raise RuntimeError(KEY)

    monkeypatch.setattr(
        tinfoil, "TinfoilAI", failing_constructor if failure == "constructor" else sdk.factory
    )
    async with appliance(hosted_settings(tmp_path)) as (client, runtime, forwarded):
        assert not runtime.healthy
        response = await client.post("/v1/chat/completions", json=request())
        assert response.status_code == 503
        health = await client.get("/aiab/health")
        assert health.status_code == 503
        assert not health.json()["classifier_verification"]["security_verified"]
        assert not forwarded and not sdk.calls
        assert KEY not in health.text + response.text + caplog.text


@pytest.mark.parametrize("anthropic", [False, True])
def test_resolved_tinfoil_overlay_preserves_hosted_security(
    tmp_path: Path, anthropic: bool
) -> None:
    compose = shlex.split(os.environ.get("COMPOSE", "docker compose"))
    if (
        not compose
        or not shutil.which(compose[0])
        or subprocess.run([*compose, "version"], capture_output=True).returncode
    ):
        pytest.skip("Docker Compose is required for resolved overlay validation")
    tinfoil_path = key_file(tmp_path)
    anthropic_path = tmp_path / "anthropic.secret"
    anthropic_path.write_text(ANTHROPIC_KEY)
    anthropic_path.chmod(0o400)
    command = [
        *compose,
        "--env-file",
        "/dev/null",
        "-f",
        str(ROOT / "deploy/docker-compose.hosted.yml"),
    ]
    if anthropic:
        command += ["-f", str(ROOT / "deploy/docker-compose.hosted.anthropic.yml")]
    environment = {
        "PATH": os.environ["PATH"],
        "AIAB_ANTHROPIC_KEY_FILE": str(anthropic_path),
        "AIAB_TINFOIL_KEY_FILE": str(tinfoil_path),
        "TINFOIL_API_KEY": KEY,
        "TINFOIL_API_KEY_FILE": "/must-not-inherit.secret",
        "ANTHROPIC_API_KEY": ANTHROPIC_KEY,
        "CLASSIFIER_MODEL": "must-not-inherit-model",
    }
    baseline = subprocess.run(
        [*command, "config", "--format", "json"],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert baseline.returncode == 0, baseline.stderr
    original = json.loads(baseline.stdout)
    command += [
        "-f",
        str(ROOT / "deploy/docker-compose.hosted.tinfoil.yml"),
        "config",
        "--format",
        "json",
    ]
    resolved = subprocess.run(
        command, env=environment, cwd=tmp_path, capture_output=True, text=True
    )
    assert resolved.returncode == 0, resolved.stderr
    assert KEY not in resolved.stdout + resolved.stderr
    assert ANTHROPIC_KEY not in resolved.stdout + resolved.stderr
    assert "must-not-inherit" not in resolved.stdout + resolved.stderr
    topology = json.loads(resolved.stdout)
    aiab = topology["services"]["aiab"]
    effective = aiab["environment"]
    assert "TINFOIL_API_KEY" not in effective
    assert effective["TINFOIL_API_KEY_FILE"] == "/run/secrets/tinfoil_api_key"
    assert effective["CLASSIFIER_MODEL"] == TINFOIL_MODEL
    assert effective["AIAB_MODE"] == "local" and effective["ENFORCEMENT_MODE"] == "block"
    assert topology["secrets"]["tinfoil_api_key"]["file"] == str(tinfoil_path)
    expected_secrets = [{"source": "tinfoil_api_key", "target": "tinfoil_api_key"}]
    if anthropic:
        assert effective["INFERENCE_PROVIDER"] == "anthropic"
        assert effective["ANTHROPIC_MODEL"] == ANTHROPIC_MODEL
        expected_secrets.insert(0, {"source": "anthropic_api_key", "target": "anthropic_api_key"})
    assert aiab["secrets"] == expected_secrets
    assert aiab.pop("tmpfs") == ["/tmp:size=256m,mode=1777,nosuid,nodev,noexec"]
    # Removing only the intended additions recovers the entire prior resolved topology:
    # no broader ports, capabilities, logging, mounts, dependencies, or browser settings.
    del topology["secrets"]["tinfoil_api_key"]
    if not topology["secrets"]:
        del topology["secrets"]
    if anthropic:
        aiab["secrets"] = expected_secrets[:1]
    else:
        del aiab["secrets"]
    effective.pop("TINFOIL_API_KEY_FILE")
    effective["CLASSIFIER_MODEL"] = "mock-keywords/v1"
    assert topology == original
    del environment["AIAB_TINFOIL_KEY_FILE"]
    missing = subprocess.run(command, env=environment, cwd=tmp_path, capture_output=True, text=True)
    assert missing.returncode != 0
    assert KEY not in missing.stdout + missing.stderr


@pytest.mark.parametrize("legacy", ["", KEY])
def test_additional_legacy_overlay_is_not_sanitized_and_config_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy: str
) -> None:
    """Arbitrary added overlays are outside the documented environment guarantees."""
    compose = shlex.split(os.environ.get("COMPOSE", "docker compose"))
    if (
        not compose
        or not shutil.which(compose[0])
        or subprocess.run([*compose, "version"], capture_output=True).returncode
    ):
        pytest.skip("Docker Compose is required for resolved overlay validation")
    extra_overlay = tmp_path / "unsupported-legacy-overlay.yml"
    extra_overlay.write_text(
        json.dumps({"services": {"aiab": {"environment": {"TINFOIL_API_KEY": legacy}}}})
    )
    resolved = subprocess.run(
        [
            *compose,
            "--env-file",
            "/dev/null",
            "-f",
            str(ROOT / "deploy/docker-compose.hosted.yml"),
            "-f",
            str(ROOT / "deploy/docker-compose.hosted.anthropic.yml"),
            "-f",
            str(extra_overlay),
            "-f",
            str(ROOT / "deploy/docker-compose.hosted.tinfoil.yml"),
            "config",
            "--format",
            "json",
        ],
        env={
            "PATH": os.environ["PATH"],
            "AIAB_ANTHROPIC_KEY_FILE": str(tmp_path / "unused-anthropic.secret"),
            "AIAB_TINFOIL_KEY_FILE": str(tmp_path / "unused-tinfoil.secret"),
        },
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert resolved.returncode == 0, resolved.stderr
    effective = json.loads(resolved.stdout)["services"]["aiab"]["environment"]
    # The value is already exposed in Compose output/environment. Claiming the
    # final overlay removes it would be false, even though app admission is closed.
    assert effective["TINFOIL_API_KEY"] == legacy
    assert effective["TINFOIL_API_KEY_FILE"] == "/run/secrets/tinfoil_api_key"

    def refuse_secret_read(*_: Any) -> None:
        pytest.fail("Ambiguous sources must fail before any credential file is read")

    monkeypatch.setattr("aiab.config.read_private_key", refuse_secret_read)
    with pytest.raises(ConfigurationError, match="only one Tinfoil credential source") as error:
        create_app(Config.from_env(effective))
    assert KEY not in str(error.value)
