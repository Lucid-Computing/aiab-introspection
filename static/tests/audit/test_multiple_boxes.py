import importlib.util
from pathlib import Path

import pytest


def release_module():
    path = Path(__file__).resolve().parents[2] / "deploy/tinfoil/audit/release.py"
    spec = importlib.util.spec_from_file_location("release_boxes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_create_box_never_updates_or_replaces_the_existing_box():
    calls = []

    class Provider:
        def request(self, method, path, value=None):
            calls.append((method, path, value))
            if method == "GET":
                return {
                    "repo": "org/config",
                    "host_name": "cpu-host",
                    "secrets": ["TOKEN"],
                    "variables": "eyJNT0RFIjoibGl2ZSJ9",
                    "debug": True,
                    "domain": "old.example",
                }
            return {"id": "new-instance", "domain": "dev.example"}

    assert release_module().create_box(Provider(), "existing", "dev", "v1.2.3", "org/config") == (
        "new-instance",
        "dev.example",
    )
    assert [(m, p) for m, p, _ in calls] == [
        ("GET", "/api/containers/existing"),
        ("POST", "/api/containers"),
    ]
    payload = calls[1][2]
    assert payload["name"] == "dev" and payload["tag"] == "v1.2.3"
    assert payload["variables"] == {"MODE": "live"}
    assert payload["secrets"] == ["TOKEN"]
    assert payload["debug"] is False and payload["disable_cc_mode"] is False
    assert payload["mark_latest_release"] is False
    assert "replace_container_id" not in payload and "custom_domain" not in payload


@pytest.mark.parametrize("name", ["default", "../production", "A BOX", "-dev", "x" * 41])
def test_bad_name_never_calls_provider(name):
    class Provider:
        def request(self, *args):
            pytest.fail("provider should not be called")

    with pytest.raises(ValueError):
        release_module().create_box(Provider(), "existing", name, "v1.2.3", "org/config")


def test_wrong_template_repo_never_creates_box():
    class Provider:
        def request(self, method, path, value=None):
            assert method == "GET"
            return {"repo": "another/repo"}

    with pytest.raises(ValueError, match="different config repository"):
        release_module().create_box(Provider(), "existing", "dev", "v1.2.3", "org/config")
