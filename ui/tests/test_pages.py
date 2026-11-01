"""Every page renders with one agreement on disk and no deploy slot, box or GitHub."""

import shutil
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from server import app as ui
from server import deploystatus
from server.config import REPO_ROOT, load_settings

LAB = {"username": "lab", "party": "lab", "display_name": "Lab"}
FIXTURE = REPO_ROOT / "static/tests/fixtures/graph-plan"


@pytest.fixture
def client(tmp_path, monkeypatch):
    agreements = tmp_path / "agreements"
    shutil.copytree(FIXTURE, agreements / "bio", ignore=shutil.ignore_patterns("trust.json"))
    settings = replace(
        load_settings(),
        agreements_dir=agreements,
        trust_file=FIXTURE / "trust.json",
        trust_follows_production=False,
        box_url=None,
        runs_dir=tmp_path / "runs",
        logs_dir=tmp_path / "logs",
        release_file=REPO_ROOT / "static/deploy/tinfoil/audit/release.json",
        demo_keys_dir=tmp_path / "keys",
    )
    monkeypatch.setattr(ui, "settings", settings)
    monkeypatch.setattr(ui, "user_of", lambda request: LAB)

    async def no_github(_settings):
        return None

    monkeypatch.setattr(deploystatus, "production_trust", no_github)
    return TestClient(ui.app, follow_redirects=False)


@pytest.mark.parametrize(
    "path",
    [
        "/how",
        "/agreements",
        "/agreements/new",
        "/runs",
        "/runs?agreement=bio",
        "/a/bio/plan",
        "/a/bio/sign",
    ],
)
def test_page_renders(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.text[-500:]


def test_runs_page_offers_the_signed_agreement_and_names_what_is_missing(client):
    html = client.get("/runs").text
    assert 'value="bio" selected' in html and "Plan to send" in html
    assert "sha256:9b16420a35b9eea517ef88e6715726425d20387967ea40ff7e22f401cc2fcc9e" in html
    assert "lab.token in" in html  # the demo keys are missing, so the run is off


def test_legacy_routes_go_to_agreements(client):
    assert client.get("/plan").headers["location"] == "/agreements"
    assert client.get("/deployed").status_code == 404
    assert client.post("/a/bio/deploy").status_code == 404


def test_run_refuses_without_an_agreement(client):
    response = client.post("/runs/start", data={"session": "x"})
    assert response.status_code == 303 and "Pick%20an%20agreement" in response.headers["location"]
