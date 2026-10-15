"""Exercise the demo's startup trust boundary and browser response behavior."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEBCHAT = ROOT / "webchat"
VALID_URLS = [
    "https://localhost:8443",
    "http://127.0.0.1:8443/",
    "https://demo.example.test",
    "https://[::1]:8443",
    "https://[2001:db8::1234]",
    "https://jonathan.quest/aiab-introspection/api",
    "https://jonathan.quest/aiab-introspection/api/",
    "http://127.0.0.1:8080/demo_1/v2.0/~api/",
    "https://[::1]:8443/aiab-introspection/api",
]
INVALID_URLS = [
    None,
    "",
    "https://",
    "https://[:::]",
    "https://[1]",
    "https://999.0.0.1",
    "https://127.01.0.1",
    "https://127.1",
    "https://0x7f.0.0.1",
    "https://0x7f000001",
    "https://2130706433",
    "https://localhost:0",
    "https://localhost:65536",
    "https://localhost:",
    "javascript:alert(1)",
    "https://secret@localhost",
    "https://user:secret@localhost/api",
    'https://localhost/";alert(1);//',
    "https://localhost/\\evil",
    "https://localhost?secret=abc",
    "https://localhost#fragment",
    "https://localhost/api?",
    "https://localhost/api#",
    "https://localhost\nhttps://evil.test",
    "https://localhost\r",
    "https://localhost\t",
    "https://localhost/api\n",
    "https://localhost/api\tmore",
    "https://localhost/api\x01",
    "https://localhost//evil.test",
    "https://localhost/api//v1",
    "https://localhost/api//",
    "https://localhost/./api",
    "https://localhost/api/../v1",
    "https://localhost/api/.",
    "https://localhost/api/..",
    "https://localhost/%2e%2e/api",
    "https://localhost/api%2f..%2f",
    "https://localhost/api/%5c",
    "https://localhost/api/%252e%252e",
    "https://localhost/api/%00",
    "https://localhost/api/%41",
    "https://localhost/api/hello world",
    "https://localhost/api/é",
    "https://localhost./api",
    "https:///localhost/api",
    "//localhost/api",
]


def test_presets_match_reviewed_plan() -> None:
    plan = (ROOT / "aiab-plan.md").read_text()
    section = plan.split("**`webchat/demo_prompts.json`:**", 1)[1]
    expected = json.loads(section.split("```json", 1)[1].split("```", 1)[0])
    assert json.loads((WEBCHAT / "demo_prompts.json").read_text()) == expected


@pytest.mark.parametrize("url", VALID_URLS)
def test_entrypoint_renders_literal_configuration(tmp_path: Path, url: str) -> None:
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    nginx = bin_path / "nginx"
    nginx.write_text("#!/bin/sh\nprintf '%s\\n' started\n")
    nginx.chmod(0o755)
    result = subprocess.run(
        ["sh", str(WEBCHAT / "entrypoint.sh")],
        env={"PATH": f"{bin_path}:/usr/bin:/bin", "WEBCHAT_ROOT": str(tmp_path), "AIAB_URL": url},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == "started\n"
    config = (tmp_path / "config.js").read_text()
    assert json.loads(config.removeprefix("window.AIAB_URL = ").removesuffix(";\n")) == url.rstrip(
        "/"
    )


@pytest.mark.parametrize("url", INVALID_URLS)
def test_entrypoint_rejects_invalid_or_injected_url(tmp_path: Path, url: str | None) -> None:
    environ = {"PATH": "/usr/bin:/bin", "WEBCHAT_ROOT": str(tmp_path)}
    if url is not None:
        environ["AIAB_URL"] = url
    result = subprocess.run(
        ["sh", str(WEBCHAT / "entrypoint.sh")],
        env=environ,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert result.stderr == (
        "AIAB_URL must be a valid HTTP(S) origin with an optional safe path prefix.\n"
    )
    assert result.stdout == ""
    assert not (tmp_path / "config.js").exists()


@pytest.mark.parametrize(
    "url",
    ["https://localhost:8443", "https://jonathan.quest/aiab-introspection/api/"],
)
def test_browser_response_handling_and_ephemeral_history(url: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the browser logic smoke test")
    html = (WEBCHAT / "index.html").read_text()
    script = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)[0]
    harness = (ROOT / "tests" / "webchat_harness.cjs").read_text()
    result = subprocess.run(
        [node, "-e", harness],
        input=script,
        env={"AIAB_TEST_URL": url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_browser_rejects_unsafe_urls_before_network_access() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the browser URL boundary test")
    html = (WEBCHAT / "index.html").read_text()
    assert '<script src="config.js">' in html
    script = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)[0]
    result = subprocess.run(
        [node, str(ROOT / "tests/webchat_urls.cjs")],
        input=json.dumps({"script": script, "valid": VALID_URLS, "invalid": INVALID_URLS}),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_demo_compose_boundaries() -> None:
    compose = shlex.split(os.environ.get("COMPOSE", "docker compose"))
    if not compose or not shutil.which(compose[0]):
        pytest.skip("Docker Compose is required for resolved topology validation")
    available = subprocess.run([*compose, "version"], capture_output=True, check=False)
    if available.returncode:
        pytest.skip("Docker Compose is unavailable")
    result = subprocess.run(
        [
            *compose,
            "--env-file",
            "/dev/null",
            "-f",
            str(ROOT / "deploy/docker-compose.demo.yml"),
            "config",
            "--format",
            "json",
        ],
        env={"PATH": os.environ["PATH"], "INFERENCE_MODEL_PATH": "/tmp/test-model.gguf"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    aiab = services["aiab"]
    assert aiab["environment"]["ENFORCEMENT_MODE"] == "block"
    assert aiab["environment"]["CORS_ALLOW_ORIGIN"] == "http://localhost:8081"
    assert aiab["environment"]["TINFOIL_API_KEY"] == ""
    assert aiab["environment"]["CLASSIFIER_MODEL"] == ""
    assert aiab["environment"]["REPORT_PERIOD_SECONDS"] == "10"
    assert aiab["ports"][0]["host_ip"] == "127.0.0.1"
    assert services["webchat"]["ports"][0]["host_ip"] == "127.0.0.1"
    assert services["webchat"]["ports"][0]["published"] == "8081"
    assert services["webchat"]["build"]["context"] == str(WEBCHAT)
    assert "ports" not in services["inference"]
    # Some Compose releases omit false-valued bind options in resolved JSON.
    # Retain the explicit source requirement and reject an enabled resolved option.
    assert "create_host_path: false" in (ROOT / "deploy/docker-compose.local.yml").read_text()
    assert services["inference"]["volumes"][0]["bind"].get("create_host_path", False) is False
    assert services["inference"]["logging"]["driver"] == "none"
