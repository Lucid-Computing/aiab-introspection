"""Ensure the installed console scripts resolve to callable Python entrypoints."""

from importlib.metadata import distribution

import pytest


@pytest.mark.parametrize("name", ["aiab", "aiab-audit", "auditor", "aiab-plan"])
def test_console_entrypoint_loads(name: str) -> None:
    entrypoints = distribution("aiab-introspection").entry_points
    matches = [
        entry for entry in entrypoints if entry.group == "console_scripts" and entry.name == name
    ]

    assert len(matches) == 1
    assert callable(matches[0].load())


def test_default_image_starts_the_signed_plan_runtime():
    from pathlib import Path

    dockerfile = Path(__file__).resolve().parents[1] / "deploy/Dockerfile"
    assert 'CMD ["python", "-m", "aiab.baked.main"]' in dockerfile.read_text()
