"""Exercise release-boundary authentication and secret-free candidate generation."""

import json
from pathlib import Path

import pytest

from aiab.config import Config, ConfigurationError
from auditor.policy import expected_manifest
from auditor.verify import VerificationError
from deploy.tinfoil.prepare_release import SETTINGS, prepare
from tests.test_core import appliance, finalized, request, settings

TOKEN = "synthetic-gateway-token-00000000000000"


def test_tinfoil_anthropic_repository_secret_is_narrowly_scoped() -> None:
    env = {
        **SETTINGS,
        "TINFOIL_API_KEY": "synthetic-classifier-key",
        "ANTHROPIC_API_KEY": "synthetic-anthropic-key",
        "AIAB_GATEWAY_TOKEN": TOKEN,
    }
    config = Config.from_env(env)
    assert config.anthropic_api_key == env["ANTHROPIC_API_KEY"]
    assert TOKEN not in repr(config)
    for change in (
        {"AIAB_MODE": "local"},
        {"ANTHROPIC_API_KEY_FILE": "/must/not/be/read"},
        {"ANTHROPIC_API_KEY": "bad\nkey"},
        {"AIAB_GATEWAY_TOKEN": ""},
        {"AIAB_GATEWAY_TOKEN": "x" * 31},
        {"AIAB_GATEWAY_TOKEN": "x" * 32 + "\u00e9"},
        {"AIAB_REQUIRE_GATEWAY_TOKEN": "TRUE"},
    ):
        with pytest.raises(ConfigurationError):
            Config.from_env({**env, **change})


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("authorization", "Bearer wrong")],
        [("authorization", f"Basic {TOKEN}")],
        [("authorization", f"Bearer {TOKEN}"), ("authorization", f"Bearer {TOKEN}")],
        [("authorization", f"Bearer {TOKEN}, Bearer {TOKEN}")],
    ],
)
async def test_unauthenticated_direct_inference_never_admits_or_forwards(headers) -> None:
    async with appliance(settings(gateway_token=TOKEN, require_gateway_token=True)) as (
        client,
        runtime,
        forwarded,
    ):
        result = await client.post("/v1/chat/completions", content=b"not-json", headers=headers)
        assert result.status_code == 401
        assert (await client.get("/v1/models", headers=headers)).status_code == 401
        assert (await client.get("/aiab/manifest")).status_code == 200
        assert not forwarded
        assert finalized(runtime)["requests_total"] == 0


async def test_gateway_token_is_accepted_but_never_forwarded_upstream() -> None:
    async with appliance(settings(gateway_token=TOKEN)) as (client, _, forwarded):
        response = await client.post(
            "/v1/chat/completions", json=request(), headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert response.status_code == 200
        assert len(forwarded) == 1
        assert "authorization" not in forwarded[0].headers


@pytest.mark.parametrize(
    "path",
    [
        "/v1/chat/completions/",
        "/%76%31/chat/completions",
        "/v1//chat/completions",
        "/V1/chat/completions",
        "/v1/chat%2fcompletions",
        "//v1/chat/completions",
    ],
)
async def test_path_variants_cannot_bypass_authentication(path: str) -> None:
    async with appliance(settings(gateway_token=TOKEN)) as (client, runtime, forwarded):
        result = await client.post(path, json=request())
        assert result.status_code in {401, 404}
        assert not forwarded
        assert finalized(runtime)["requests_total"] == 0


def test_anthropic_policy_is_offline_and_auth_changes_digest(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-do-not-read")
    monkeypatch.setattr("aiab.config.read_private_key", lambda *_: pytest.fail("file read"))
    manifest = expected_manifest(SETTINGS)
    assert manifest["inference_provider"] == "anthropic"
    assert manifest["inference_model"] == "claude-sonnet-4-6"
    assert manifest["gateway_authentication"] == "bearer"
    assert "ambient-do-not-read" not in json.dumps(manifest)
    assert expected_manifest({**SETTINGS, "AIAB_REQUIRE_GATEWAY_TOKEN": "false"}) != manifest
    for name in ("AIAB_GATEWAY_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY_FILE"):
        with pytest.raises(VerificationError):
            expected_manifest({**SETTINGS, name: "secret"})


def test_release_candidate_contains_only_explicit_public_files(tmp_path: Path) -> None:
    output = tmp_path / "candidate"
    digest = prepare(output, "ghcr.io/example/aiab", None)
    assert len(digest) == 64
    assert {str(path.relative_to(output)) for path in output.rglob("*") if path.is_file()} == {
        "tinfoil-config.yml",
        "policy-settings.json",
        "policy-manifest.json",
        "policy-digest.txt",
        "README.md",
        ".github/workflows/measure-release.yml",
    }
    config = (output / "tinfoil-config.yml").read_text()
    assert 'AIAB_REQUIRE_GATEWAY_TOKEN: "true"' in config
    assert "ANTHROPIC_API_KEY_FILE" not in config
    assert "ghcr.io/example/aiab@sha256:" + "0" * 64 in config
    assert "REPLACE_" not in config
    assert "offline-policy" not in "".join(p.read_text() for p in output.rglob("*") if p.is_file())
    with pytest.raises(FileExistsError):
        prepare(output, "ghcr.io/example/aiab", "sha256:" + "1" * 64)


@pytest.mark.parametrize("digest", ["latest", "sha256:" + "0" * 64, "sha256:" + "a" * 63])
def test_invalid_release_digest_creates_nothing(tmp_path: Path, digest: str) -> None:
    output = tmp_path / "candidate"
    with pytest.raises(ValueError):
        prepare(output, "ghcr.io/example/aiab", digest)
    assert not output.exists()
