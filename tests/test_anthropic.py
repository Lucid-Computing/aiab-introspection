"""Exercise hosted Anthropic translation/privacy using only synthetic keys and mock HTTP."""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from aiab.anthropic import MAX_RESPONSE_BYTES
from aiab.config import ANTHROPIC_MODEL, ANTHROPIC_ORIGIN, Config, ConfigurationError
from aiab.proxy import create_app
from tests.test_core import CAPABILITIES, appliance, finalized, settings

KEY = "synthetic-unit-test-credential-never-valid-at-provider"
PRIVATE = "private-upstream-error-marker"
ROOT = Path(__file__).resolve().parents[1]


def anthropic_settings(**changes: Any) -> Config:
    return settings(
        inference_provider="anthropic",
        inference_url=ANTHROPIC_ORIGIN,
        anthropic_api_key=KEY,
        **changes,
    )


def request(**changes: Any) -> dict[str, Any]:
    return {
        "model": ANTHROPIC_MODEL,
        "messages": [{"role": "user", "content": "Suggest dinner"}],
        **changes,
    }


def success(**changes: Any) -> dict[str, Any]:
    return {
        "id": PRIVATE,
        "type": "message",
        "role": "assistant",
        "model": ANTHROPIC_MODEL,
        "content": [{"type": "text", "text": "Vegetable "}, {"type": "text", "text": "curry"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 15, "output_tokens": 5},
        **changes,
    }


def key_file(tmp_path: Path, content: bytes = KEY.encode(), mode: int = 0o400) -> Path:
    path = tmp_path / "credential.secret"
    path.write_bytes(content)
    path.chmod(mode)
    return path


@pytest.mark.parametrize("ending", [b"", b"\n"])
def test_config_reads_private_file_once_and_hides_key(tmp_path: Path, ending: bytes) -> None:
    path = key_file(tmp_path, KEY.encode() + ending)
    config = Config.from_env(
        {"INFERENCE_PROVIDER": "anthropic", "ANTHROPIC_API_KEY_FILE": str(path)}
    )
    path.unlink()
    assert config.anthropic_api_key == KEY
    assert config.anthropic_model == ANTHROPIC_MODEL
    assert config.inference_url == ANTHROPIC_ORIGIN
    assert KEY not in repr(config)


@pytest.mark.parametrize(
    "content", [b"", b"\n", b"abc\n\n", b"abc\r\n", b"a b", b"a\x00b", b"x" * 4098, b"\xff"]
)
def test_invalid_key_bytes_fail_without_reflecting_secret(tmp_path: Path, content: bytes) -> None:
    path = key_file(tmp_path, content)
    with pytest.raises(ConfigurationError) as failure:
        Config.from_env({"INFERENCE_PROVIDER": "anthropic", "ANTHROPIC_API_KEY_FILE": str(path)})
    assert str(path) not in str(failure.value)
    assert KEY not in str(failure.value)


@pytest.mark.parametrize("mode", [0o644, 0o440, 0o640, 0o700, 0o4755])
def test_insecure_key_modes_fail(tmp_path: Path, mode: int) -> None:
    path = key_file(tmp_path, mode=mode)
    with pytest.raises(ConfigurationError):
        Config.from_env({"INFERENCE_PROVIDER": "anthropic", "ANTHROPIC_API_KEY_FILE": str(path)})


def test_missing_relative_symlink_directory_and_fifo_keys_fail(tmp_path: Path) -> None:
    target = key_file(tmp_path)
    linked = tmp_path / "link"
    linked.symlink_to(target)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, mode=0o600)
    for filename in (
        "",
        "relative.secret",
        str(linked),
        str(tmp_path),
        str(fifo),
        str(tmp_path / "missing"),
    ):
        with pytest.raises(ConfigurationError):
            Config.from_env({"INFERENCE_PROVIDER": "anthropic", "ANTHROPIC_API_KEY_FILE": filename})


def test_untrusted_secret_owner_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = key_file(tmp_path)
    if path.stat().st_uid == 0:
        pytest.skip("Root-owned secret files are explicitly allowed")
    monkeypatch.setattr(os, "geteuid", lambda: path.stat().st_uid + 1)
    with pytest.raises(ConfigurationError):
        Config.from_env({"INFERENCE_PROVIDER": "anthropic", "ANTHROPIC_API_KEY_FILE": str(path)})


async def test_default_client_disables_proxy_environment_and_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}
    constructor = httpx.AsyncClient

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        captured.update(kwargs)
        return constructor(**kwargs, transport=httpx.MockTransport(lambda _: httpx.Response(500)))

    monkeypatch.setenv("HTTPS_PROXY", "https://must-not-use.test")
    monkeypatch.setenv("SSL_CERT_FILE", "/must-not-read.crt")
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    app = create_app(anthropic_settings())
    async with app.router.lifespan_context(app):
        assert captured == {"follow_redirects": False, "trust_env": False}


def test_provider_selection_and_endpoint_fail_closed(tmp_path: Path) -> None:
    path = key_file(tmp_path)
    environment = {"INFERENCE_PROVIDER": "anthropic", "ANTHROPIC_API_KEY_FILE": str(path)}
    for extra in (
        {"INFERENCE_PROVIDER": "typo"},
        {"ANTHROPIC_API_KEY": KEY},
        {"INFERENCE_URL": "http://api.anthropic.com"},
        {"INFERENCE_URL": "https://api.anthropic.com.evil.test"},
        {"INFERENCE_URL": "https://user:password@api.anthropic.com"},
        {"INFERENCE_URL": "https://api.anthropic.com:444"},
        {"ANTHROPIC_MODEL": "unsafe model"},
    ):
        with pytest.raises(ConfigurationError) as failure:
            Config.from_env({**environment, **extra})
        assert KEY not in str(failure.value)
    with pytest.raises(ConfigurationError):
        Config.from_env({"ANTHROPIC_API_KEY_FILE": str(path)})
    assert Config.from_env({"ANTHROPIC_API_KEY": KEY}).inference_provider == "openai"
    with pytest.raises(ConfigurationError):
        replace(anthropic_settings(), inference_provider="openai").validate()


@pytest.mark.parametrize(
    "stop, finish",
    [
        ("end_turn", "stop"),
        ("stop_sequence", "stop"),
        ("max_tokens", "length"),
        ("refusal", "content_filter"),
    ],
)
async def test_translation_and_public_response_keep_secrets_on_server(
    stop: str, finish: str
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=success(stop_reason=stop),
            headers={"set-cookie": KEY, "x-private": PRIVATE, "authorization": KEY},
        )

    body = request(
        messages=[
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": [{"type": "text", "text": "Dinner?"}]},
            {"role": "assistant", "content": "What ingredients?"},
            {"role": "user", "content": "Chickpeas"},
        ],
        max_completion_tokens=50,
        temperature=0.3,
        stop=["END"],
        n=1,
    )
    async with appliance(anthropic_settings(), handler=handler) as (client, runtime, forwarded):
        response = await client.post(
            "/v1/chat/completions",
            json=body,
            headers={"authorization": "Bearer browser-only", "cookie": "browser-only"},
        )
        assert response.status_code == 200
        result = response.json()
        assert result["choices"] == [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Vegetable curry"},
                "finish_reason": finish,
            }
        ]
        assert result["usage"] == {"prompt_tokens": 15, "completion_tokens": 5, "total_tokens": 20}
        assert result["model"] == ANTHROPIC_MODEL
        assert result["object"] == "chat.completion"
        assert result["id"].startswith("chatcmpl-")
        upstream = forwarded[0]
        assert str(upstream.url) == ANTHROPIC_ORIGIN + "/v1/messages"
        assert upstream.headers["x-api-key"] == KEY
        assert upstream.headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in upstream.headers and "cookie" not in upstream.headers
        payload = json.loads(upstream.content)
        assert payload == {
            "model": ANTHROPIC_MODEL,
            "system": [{"type": "text", "text": "Be concise."}],
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Dinner?"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "What ingredients?"}]},
                {"role": "user", "content": [{"type": "text", "text": "Chickpeas"}]},
            ],
            "stream": False,
            "max_tokens": 50,
            "temperature": 0.3,
            "stop_sequences": ["END"],
        }
        for path in (
            "/v1/models",
            "/aiab/health",
            "/aiab/manifest",
            "/aiab/pubkey",
            "/aiab/reports",
        ):
            public = await client.get(path)
            assert KEY not in public.text and PRIVATE not in public.text
        assert len(forwarded) == 1  # Listing the configured model does not spend API credits.
        assert KEY not in response.text and PRIVATE not in response.text
        assert "set-cookie" not in response.headers and "x-private" not in response.headers
        assert finalized(runtime)["requests_forwarded"] == 1


@pytest.mark.parametrize(
    "cache_usage, prompt_tokens",
    [
        ({}, 15),
        ({"cache_creation_input_tokens": None, "cache_read_input_tokens": None}, 15),
        ({"cache_creation_input_tokens": None, "cache_read_input_tokens": 7}, 22),
        ({"cache_creation_input_tokens": 3, "cache_read_input_tokens": 7}, 25),
    ],
)
async def test_optional_cache_usage_normalizes_absent_and_null_to_zero(
    cache_usage: dict[str, int | None], prompt_tokens: int
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=success(usage={"input_tokens": 15, "output_tokens": 5, **cache_usage})
        )

    async with appliance(anthropic_settings(), handler=handler) as (client, _, forwarded):
        response = await client.post("/v1/chat/completions", json=request())
        assert response.status_code == 200
        result = response.json()
        assert result["object"] == "chat.completion"
        assert result["model"] == ANTHROPIC_MODEL
        assert result["choices"] == [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Vegetable curry"},
                "finish_reason": "stop",
            }
        ]
        assert result["usage"] == {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": 5,
            "total_tokens": prompt_tokens + 5,
        }
        assert len(forwarded) == 1


@pytest.mark.parametrize("field", ["cache_creation_input_tokens", "cache_read_input_tokens"])
@pytest.mark.parametrize("value", ["0", False, {}, -1])
async def test_optional_cache_usage_still_rejects_invalid_values(field: str, value: Any) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=success(usage={"input_tokens": 15, "output_tokens": 5, field: value})
        )

    async with appliance(anthropic_settings(), handler=handler) as (client, _, _):
        response = await client.post("/v1/chat/completions", json=request())
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "upstream_unavailable"


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
@pytest.mark.parametrize("missing", [False, True])
async def test_required_token_usage_rejects_null_or_missing(field: str, missing: bool) -> None:
    usage = {"input_tokens": 15, "output_tokens": 5, field: None}
    if missing:
        del usage[field]

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=success(usage=usage))

    async with appliance(anthropic_settings(), handler=handler) as (client, _, _):
        response = await client.post("/v1/chat/completions", json=request())
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "upstream_unavailable"


@pytest.mark.parametrize(
    "change",
    [
        {"model": "claude-opus-4-6"},
        {"stream": True},
        {"stream": "false"},
        {"tools": []},
        {"n": 2},
        {"n": True},
        {"max_tokens": 2049},
        {"max_tokens": 0},
        {"max_tokens": True},
        {"max_tokens": 10, "max_completion_tokens": 10},
        {"temperature": 1.1},
        {"temperature": 0.3, "top_p": 0.8},
        {"top_p": -0.1},
        {"seed": 3},
        {"presence_penalty": 0},
        {"stop": ["a"] * 5},
        {"stop": ""},
        {"stop": "x" * 257},
        {
            "messages": [
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}
            ]
        },
        {"messages": [{"role": "tool", "content": "x"}]},
        {"messages": [{"role": "developer", "content": "x"}, {"role": "user", "content": "Hi"}]},
        {"messages": [{"role": "user", "content": "Hi"}, {"role": "system", "content": "x"}]},
        {
            "messages": [
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "prefix"},
            ]
        },
        {"messages": [{"role": "assistant", "content": "Hi"}, {"role": "user", "content": "x"}]},
        {"messages": [{"role": "user", "content": " "}]},
        {"messages": [{"role": "user", "content": "Hi", "name": "extra"}]},
    ],
)
async def test_unsupported_requests_never_reach_provider(change: dict[str, Any]) -> None:
    async with appliance(anthropic_settings()) as (client, runtime, forwarded):
        response = await client.post("/v1/chat/completions", json=request(**change))
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "unsupported_request"
        assert not forwarded and finalized(runtime)["requests_total"] == 0


async def test_policy_block_never_reaches_anthropic() -> None:
    async with appliance(anthropic_settings(enforcement_mode="block")) as (
        client,
        runtime,
        forwarded,
    ):
        result = await client.post(
            "/v1/chat/completions",
            json=request(messages=[{"role": "user", "content": CAPABILITIES}]),
        )
        assert result.status_code == 403
        assert not forwarded and finalized(runtime)["requests_blocked"] == 1


@pytest.mark.parametrize(
    "upstream_status, expected",
    [
        (301, 502),
        (400, 502),
        (401, 502),
        (403, 502),
        (429, 429),
        (500, 502),
        (503, 503),
        (529, 503),
        (504, 504),
    ],
)
async def test_provider_errors_and_redirects_never_leak_or_retry(
    upstream_status: int, expected: int, caplog: pytest.LogCaptureFixture
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            upstream_status,
            json={"error": {"message": KEY + PRIVATE}},
            headers={"location": "https://evil.test/", "authorization": KEY, "x-private": PRIVATE},
        )

    with caplog.at_level(logging.DEBUG):
        async with appliance(anthropic_settings(log_level="DEBUG"), handler=handler) as (
            client,
            _,
            forwarded,
        ):
            response = await client.post("/v1/chat/completions", json=request())
            assert response.status_code == expected
            assert len(forwarded) == 1
    assert KEY not in response.text + str(response.headers) + caplog.text
    assert PRIVATE not in response.text + str(response.headers) + caplog.text


@pytest.mark.parametrize(
    "change",
    [
        {"content": [{"type": "tool_use", "input": KEY}]},
        {"content": [{"type": "text", "text": KEY}]},
        {"role": "user"},
        {"model": "other-model"},
        {"usage": {"input_tokens": True, "output_tokens": 1}},
        {"stop_reason": "tool_use"},
        {"usage": None},
    ],
)
async def test_invalid_provider_success_fails_safely(change: dict[str, Any]) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=success(**change))

    async with appliance(anthropic_settings(), handler=handler) as (client, _, _):
        response = await client.post("/v1/chat/completions", json=request())
        assert response.status_code == 502
        assert KEY not in response.text


@pytest.mark.parametrize("timeout", [True, False])
async def test_transport_failures_are_sanitized(
    timeout: bool, caplog: pytest.LogCaptureFixture
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        failure = httpx.ReadTimeout if timeout else httpx.ConnectError
        raise failure(KEY + PRIVATE)

    async with appliance(anthropic_settings(), handler=handler) as (client, _, forwarded):
        response = await client.post("/v1/chat/completions", json=request())
        assert response.status_code == (504 if timeout else 502)
        assert len(forwarded) == 1
        assert KEY not in response.text + caplog.text and PRIVATE not in response.text + caplog.text


async def test_response_size_cap_stops_reading_stream_and_closes_it() -> None:
    class HugeStream(httpx.AsyncByteStream):
        read = 0
        closed = False

        async def __aiter__(self):
            for _ in range(100):
                self.read += 1
                yield b"x" * (MAX_RESPONSE_BYTES // 2)

        async def aclose(self):
            self.closed = True

    stream = HugeStream()

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    async with appliance(anthropic_settings(), handler=handler) as (client, _, _):
        assert (await client.post("/v1/chat/completions", json=request())).status_code == 502
        assert stream.read == 3 and stream.closed


@pytest.mark.parametrize(
    "headers, body",
    [
        ({"content-encoding": "gzip"}, b""),
        ({"content-length": str(MAX_RESPONSE_BYTES + 1)}, b""),
        ({}, b"not JSON"),
        ({}, b"x" * (MAX_RESPONSE_BYTES + 1)),
    ],
)
async def test_unsafe_or_malformed_provider_bodies_fail(
    headers: dict[str, str], body: bytes
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=headers, content=body)

    async with appliance(anthropic_settings(), handler=handler) as (client, _, _):
        assert (await client.post("/v1/chat/completions", json=request())).status_code == 502


def test_overlay_only_mounts_file_secret_into_aiab(tmp_path: Path) -> None:
    compose = shlex.split(os.environ.get("COMPOSE", "docker compose"))
    if (
        not compose
        or not shutil.which(compose[0])
        or subprocess.run([*compose, "version"], capture_output=True).returncode
    ):
        pytest.skip("Docker Compose is required for resolved overlay validation")
    path = key_file(tmp_path)
    command = [
        *compose,
        "--env-file",
        "/dev/null",
        "-f",
        str(ROOT / "deploy/docker-compose.hosted.yml"),
        "-f",
        str(ROOT / "deploy/docker-compose.hosted.anthropic.yml"),
        "config",
        "--format",
        "json",
    ]
    environment = {
        "PATH": os.environ["PATH"],
        "AIAB_ANTHROPIC_KEY_FILE": str(path),
        "ANTHROPIC_API_KEY": KEY,
        "TINFOIL_API_KEY": KEY,
    }
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=" + KEY + "\n")
    result = subprocess.run(command, cwd=tmp_path, env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert KEY not in result.stdout + result.stderr
    topology = json.loads(result.stdout)
    aiab = topology["services"]["aiab"]
    assert aiab.get("depends_on", {}) == {}
    assert aiab["environment"]["ANTHROPIC_API_KEY_FILE"] == "/run/secrets/anthropic_api_key"
    assert "ANTHROPIC_API_KEY" not in aiab["environment"]
    assert aiab["environment"]["ANTHROPIC_MODEL"] == ANTHROPIC_MODEL
    assert aiab["environment"]["INFERENCE_URL"] == ANTHROPIC_ORIGIN
    assert aiab["secrets"] == [{"source": "anthropic_api_key", "target": "anthropic_api_key"}]
    assert topology["secrets"]["anthropic_api_key"]["file"] == str(path)
    assert "secrets" not in topology["services"]["webchat"]
    assert set(topology["services"]) == {"aiab", "webchat"}
    assert aiab["logging"]["driver"] == "none"
    assert aiab["read_only"] is True
    assert aiab["ports"][0]["host_ip"] == "127.0.0.1"
    missing = subprocess.run(
        command, cwd=tmp_path, env={"PATH": os.environ["PATH"]}, capture_output=True
    )
    assert missing.returncode != 0


def test_secret_paths_are_excluded_from_source_and_browser_builds() -> None:
    # The server image uses an allowlisted context, and neither image accepts a key ARG/ENV.
    ignore = (ROOT / ".dockerignore").read_text()
    rules = [line for line in ignore.splitlines() if line and not line.startswith("#")]
    assert rules[0] == "**"
    assert not any("secret" in line or ".env" in line for line in rules if line.startswith("!"))
    for relative in (
        "deploy/Dockerfile",
        "webchat/Dockerfile",
        "webchat/entrypoint.sh",
        "webchat/index.html",
    ):
        source = (ROOT / relative).read_text()
        assert "ANTHROPIC_API_KEY" not in source
        assert "AIAB_ANTHROPIC_KEY_FILE" not in source
    if not shutil.which("git"):
        pytest.skip("Git is required to verify credential ignore rules")
    ignored = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        cwd=ROOT,
        input="secrets/anthropic\nanthropic.secret\n.env.private\n",
        capture_output=True,
        text=True,
    )
    assert ignored.returncode == 0
    assert set(ignored.stdout.splitlines()) == {
        "secrets/anthropic",
        "anthropic.secret",
        ".env.private",
    }
