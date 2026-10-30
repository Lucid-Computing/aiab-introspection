"""release.py reads what differs between environments from the branch's release.json."""

import importlib.util
import pathlib

import pytest

STATIC = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "release_env", STATIC / "deploy/tinfoil/audit/release.py"
)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def test_production_pins_without_the_new_fields_keep_the_literals():
    instance, image = release.environment({"repo": release.PRODUCTION_REPO})
    assert instance == release.PRODUCTION_INSTANCE
    assert image == release.PRODUCTION_IMAGE


def test_fields_in_release_json_win_and_the_flag_wins_over_them():
    prior = {
        "repo": "Lucid-Computing/aiab-tinfoil-staging-config",
        "instance": "11111111-2222-3333-4444-555555555555",
        "image": "ghcr.io/lucid-computing/aiab-introspection-enclave-staging",
    }
    assert release.environment(prior) == (prior["instance"], prior["image"])
    flag = "99999999-2222-3333-4444-555555555555"
    assert release.environment(prior, flag)[0] == flag


def test_a_new_environment_has_no_instance_yet():
    prior = {
        "repo": "Lucid-Computing/aiab-tinfoil-staging-config",
        "image": "ghcr.io/lucid-computing/aiab-introspection-enclave-staging",
    }
    assert release.environment(prior) == (None, prior["image"])


def test_a_new_environment_needs_an_image_name():
    with pytest.raises(ValueError):
        release.environment({"repo": "Lucid-Computing/aiab-tinfoil-staging-config"})
    with pytest.raises(ValueError):
        release.environment({"repo": "x/y", "image": "docker.io/not/ghcr"})


def test_instance_host_and_secret_names():
    assert release.instance_host({"id": "x", "domain": "aiab-staging.example.dev"}) == (
        "aiab-staging.example.dev"
    )
    with pytest.raises(RuntimeError):
        release.instance_host({"id": "x", "host_name": "inf13"})
    config = {"containers": [{"name": "aiab", "secrets": ["TINFOIL_API_KEY", "AIAB_LAB_TOKEN"]}]}
    assert release.secret_names(config) == ["TINFOIL_API_KEY", "AIAB_LAB_TOKEN"]
    assert release.secret_names({"containers": []}) == []


@pytest.mark.parametrize("status", ["deploying", "started", "running", "ready"])
def test_create_does_not_start_an_instance_twice(status):
    instance = "11111111-2222-3333-4444-555555555555"
    calls = []

    class ControlPlane:
        def request(self, method, path, value=None):
            calls.append((method, path))
            return {"id": instance, "status": status, "domain": "dev.example.com"}

    assert release.create_instance(ControlPlane(), "dev", "owner/repo", "v0.0.1", []) == (
        instance, "dev.example.com"
    )
    assert (("POST", "/api/containers/" + instance + "/deploy") in calls) == (
        status == "ready"
    )
