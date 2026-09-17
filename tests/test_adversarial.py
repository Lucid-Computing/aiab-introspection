"""The adversarial driver defaults to narration, with explicit bounded execution."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "demo" / "adversarial.sh"


def run_driver(*args: str) -> subprocess.CompletedProcess[str]:
    # No deployment, model, API credentials, or repository-local environment file.
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), *args],
        cwd="/tmp",
        env={"PATH": "/usr/bin:/bin", "TINFOIL_API_KEY": "SECRET_SENTINEL_DO_NOT_PRINT"},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_adversarial_dry_run_and_syntax() -> None:
    result = run_driver()
    assert result.returncode == 0, result.stderr
    assert "DRY RUN: no commands executed" in result.stdout
    for stage in ("TAMPER", "DEBUG", "OVERRIDE", "BYPASS", "POLICY", "RESTART"):
        assert stage in result.stdout
    assert "auditor alone cannot detect bypass" in result.stdout
    assert "does not recover lost reports" in result.stdout
    assert "same OCI image" in result.stdout
    assert "SECRET_SENTINEL" not in result.stdout + result.stderr
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


@pytest.mark.parametrize(
    "args",
    [
        ("--stage", "unknown"),
        ("--stage",),
        ("--execute", "--execute"),
        ("--stage", "tamper", "--stage", "debug"),
        ("--unknown", "SECRET_SENTINEL_DO_NOT_PRINT"),
        ("--execute",),
    ],
)
def test_adversarial_rejects_invalid_arguments_without_echo(args: tuple[str, ...]) -> None:
    result = run_driver(*args)
    assert result.returncode != 0
    assert "ERROR:" in result.stderr
    assert "SECRET_SENTINEL" not in result.stdout + result.stderr


def test_override_exercises_real_startup_guard() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--execute", "--stage", "override"],
        cwd=ROOT,
        env={**os.environ, "UV_PYTHON": "3.12"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "DETECTED: CLASSIFIER_ENDPOINT_OVERRIDE is unsupported" in result.stdout
