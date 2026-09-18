"""Validate the hosted topology and exercise the real Caddy routing boundary."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import socket
import ssl
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from aiab.keys import ephemeral_tls_files

ROOT = Path(__file__).resolve().parents[1]


def test_hosted_compose_is_loopback_only_and_does_not_inherit_secrets(tmp_path: Path) -> None:
    compose = shlex.split(os.environ.get("COMPOSE", "docker compose"))
    if not compose or not shutil.which(compose[0]):
        pytest.skip("Docker Compose is required for resolved topology validation")
    if subprocess.run([*compose, "version"], capture_output=True, check=False).returncode:
        pytest.skip("Docker Compose is unavailable")
    (tmp_path / ".env").write_text("INFERENCE_MODEL_PATH=/must-not-load.env.gguf\n")
    result = subprocess.run(
        [
            *compose,
            "--env-file",
            "/dev/null",
            "-f",
            str(ROOT / "deploy/docker-compose.hosted.yml"),
            "config",
            "--format",
            "json",
        ],
        cwd=tmp_path,
        env={
            "PATH": os.environ["PATH"],
            "TINFOIL_API_KEY": "must-not-inherit-key",
            "CLASSIFIER_MODEL": "must-not-inherit-model",
            "AIAB_URL": "https://must-not-inherit.test",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "must-not-" not in result.stdout
    services = json.loads(result.stdout)["services"]
    assert set(services) == {"inference", "aiab", "webchat"}
    assert all(service["restart"] == "unless-stopped" for service in services.values())
    assert all(service["logging"]["driver"] == "none" for service in services.values())
    assert "ports" not in services["inference"]
    for name, port in [("aiab", 8443), ("webchat", 8081)]:
        assert len(services[name]["ports"]) == 1
        assert services[name]["ports"][0]["host_ip"] == "127.0.0.1"
        assert services[name]["ports"][0]["published"] == str(port)
        assert services[name]["ports"][0]["target"] == port
    environment = services["aiab"]["environment"]
    assert environment["AIAB_MODE"] == "local"
    assert environment["ENFORCEMENT_MODE"] == "block"
    assert environment["CLASSIFIER_MODEL"] == "mock-keywords/v1"
    assert environment["TINFOIL_API_KEY"] == ""
    assert environment["CORS_ALLOW_ORIGIN"] == ""
    assert services["webchat"]["environment"]["AIAB_URL"] == (
        "https://jonathan.quest/aiab-introspection/api"
    )


def test_caddy_preserves_route_boundaries_and_limits_overload(tmp_path: Path) -> None:
    caddy = shutil.which("caddy")
    if not caddy or not hasattr(os, "memfd_create"):
        pytest.skip("Caddy and Linux ephemeral TLS are required for route validation")
    released = threading.Event()
    saturated = threading.Event()
    arrivals = 0
    lock = threading.Lock()

    class Backend(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            nonlocal arrivals
            if self.path == "/hold":
                with lock:
                    arrivals += 1
                    if arrivals == 4:
                        saturated.set()
                released.wait(10)
            body = json.dumps({"path": self.path, "cookie": self.headers.get("Cookie")}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            pass

    backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    ui = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    with ephemeral_tls_files() as (certificate, key):
        tls.load_cert_chain(certificate, key)
    backend.socket = tls.wrap_socket(backend.socket, server_side=True)
    for server in (backend, ui):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    fragment = (ROOT / "deploy/caddy/aiab-introspection.caddy").read_text()
    fragment = fragment.replace("127.0.0.1:8443", f"127.0.0.1:{backend.server_port}")
    fragment = fragment.replace("127.0.0.1:8081", f"127.0.0.1:{ui.server_port}")
    config = tmp_path / "Caddyfile"
    config.write_text(
        "{\n admin off\n auto_https off\n persist_config off\n}\n"
        f"http://127.0.0.1:{port} {{\n bind 127.0.0.1\n{fragment}\n"
        'handle {\n respond "existing apex" 200\n}\n}\n'
        f"http://files.jonathan.quest:{port} {{\n bind 127.0.0.1\n"
        'respond "existing files" 200\n}\n'
    )
    adapted = subprocess.run(
        [caddy, "adapt", "--validate", "--config", str(config), "--adapter", "caddyfile"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert adapted.returncode == 0, adapted.stderr
    data = json.loads(adapted.stdout)
    assert '"max_size": 1000000' in json.dumps(data)
    with (tmp_path / "caddy.log").open("w+") as log:
        process = subprocess.Popen(
            [caddy, "run", "--config", str(config), "--adapter", "caddyfile"],
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
                for _ in range(100):
                    try:
                        client.get("/", timeout=0.1)
                        break
                    except httpx.TransportError:
                        time.sleep(0.02)
                else:
                    pytest.fail("Temporary Caddy did not start")
                assert client.get("/").text == "existing apex"
                assert client.get("/other").text == "existing apex"
                assert client.get("/aiab-introspection-other").text == "existing apex"
                assert client.get("/", headers={"Host": "files.jonathan.quest"}).text == (
                    "existing files"
                )
                redirect = client.get("/aiab-introspection")
                assert redirect.status_code == 308
                assert redirect.headers["location"] == "/aiab-introspection/"
                for asset in ("", "index.html", "config.js", "demo_prompts.json"):
                    response = client.get(f"/aiab-introspection/{asset}")
                    assert response.json()["path"] == f"/{asset}"
                assert client.get("/aiab-introspection/private.txt").status_code == 404
                assert client.get("/aiab-introspection/api-extra/aiab/health").status_code == 404
                response = client.get(
                    "/aiab-introspection/api/aiab/health?probe=1", headers={"Cookie": "secret"}
                )
                assert response.json() == {"path": "/aiab/health?probe=1", "cookie": None}
                with ThreadPoolExecutor(max_workers=4) as pool:
                    pending = [
                        pool.submit(client.get, "/aiab-introspection/api/hold", timeout=15)
                        for _ in range(4)
                    ]
                    try:
                        assert saturated.wait(5)
                        assert client.get("/aiab-introspection/api/extra").status_code == 503
                        assert client.get("/aiab-introspection/").status_code == 200
                    finally:
                        released.set()
                    assert all(request.result().status_code == 200 for request in pending)
        finally:
            released.set()
            process.terminate()
            process.wait(timeout=10)
            for server in (backend, ui):
                server.shutdown()
                server.server_close()
