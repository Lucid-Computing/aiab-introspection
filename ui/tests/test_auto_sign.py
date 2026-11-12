"""With AIAB_LAB_KEY set, the server signs every plan version as the lab by itself.

The lab signature then never freezes the plan (the server signs the next version too);
only the auditor's signature does. A key production does not trust signs nothing and
the Sign screen says why. Without the key nothing changes."""

import base64
import json
from dataclasses import replace
from urllib.parse import unquote

import pytest
from aiab.audit.protocol import sign_bytes, verify_signature
from aiab.baked.bundle import canonical, certified_key, make_certificate
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from server import deploystatus
from server import plan_view as pv
from server.auth import Account
from server.config import REPO_ROOT, load_settings

FIXTURE = REPO_ROOT / "static/tests/fixtures/graph-plan"
LAB = {"username": "teddy", "party": "lab"}
AUDITOR = {"username": "caleb", "party": "auditor"}


def raw(key: Ed25519PrivateKey) -> str:
    return base64.b64encode(key.private_bytes_raw()).decode()


def public(key: Ed25519PrivateKey) -> str:
    return base64.b64encode(key.public_key().public_bytes_raw()).decode()


@pytest.fixture
def keys(tmp_path):
    """A root per party in the trust file (model from the fixture), and a signing key per
    party with the certificate its root made for it."""
    roots = {role: Ed25519PrivateKey.generate() for role in ("lab", "auditor")}
    lab, auditor = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    trust = json.loads((FIXTURE / "trust.json").read_text())
    trust["lab_key"], trust["auditor_key"] = public(roots["lab"]), public(roots["auditor"])
    (tmp_path / "trust.json").write_text(json.dumps(trust))
    certs = {
        "lab": make_certificate(roots["lab"], "lab", public(lab)),
        "auditor": make_certificate(roots["auditor"], "auditor", public(auditor)),
    }
    return {
        "lab": lab,
        "auditor": auditor,
        "roots": roots,
        "certs": certs,
        "trust": tmp_path / "trust.json",
    }


def cert_text(keys, role):
    return canonical(keys["certs"][role]).decode()


@pytest.fixture
def settings(tmp_path, keys, monkeypatch):
    async def no_github(_settings):
        return None

    monkeypatch.setattr(deploystatus, "production_trust", no_github)
    return replace(
        load_settings(),
        agreements_dir=tmp_path / "agreements",
        trust_file=keys["trust"],
        trust_follows_production=False,
        box_url=None,
        push_drafts=False,
        lab_signing_key=raw(keys["lab"]),
        lab_signing_certificate=cert_text(keys, "lab"),
    )


def create(settings, name="auto"):
    errors, aid = pv.new_agreement(settings, {"plan_id": name, "problem": "Leaks?"}, AUDITOR)
    assert not errors
    return pv.for_agreement(settings, aid)


def lab_signature_verifies(target, keys):
    text = (target.plan_dir / "signatures/lab.sig").read_text().strip()
    digest = pv.file_digest(pv.bundle_files(target.plan_dir))
    verify_signature(public(keys["lab"]), text, "plan-bundle", {"plan_digest": digest})
    stored = json.loads((target.plan_dir / "signatures/lab.cert").read_text())
    assert stored == keys["certs"]["lab"]  # the certificate travels with the signature
    return text


def sign_as_auditor(target, keys):
    digest = pv.file_digest(pv.bundle_files(target.plan_dir))
    sig = base64.b64encode(keys["auditor"].sign(sign_bytes("plan-bundle", {"plan_digest": digest})))
    status, _ = pv.write_signature(target, "auditor", sig, AUDITOR, cert_text(keys, "auditor"))
    assert status == "ok"


def client_for(monkeypatch, settings, party="auditor"):
    from server import app as web

    monkeypatch.setattr(web, "settings", settings)
    client = TestClient(web.app, follow_redirects=False)
    client.cookies.set(web.COOKIE, web.signer.issue(Account("caleb", party, "Caleb", "unused")))
    return client


@pytest.fixture
def issuer(settings, keys):
    return replace(settings, auditor_root_key=raw(keys["roots"]["auditor"]))


def browser_key():
    return public(Ed25519PrivateKey.generate())


def test_the_server_certifies_a_browser_key_the_box_accepts(issuer, keys):
    signing = Ed25519PrivateKey.generate()
    status, certificate = pv.issue_certificate(issuer, "auditor", public(signing), AUDITOR)
    assert status == "ok"
    trust = json.loads(keys["trust"].read_text())
    assert certified_key(certificate, "auditor", trust) == public(signing)
    record = json.loads(pv.issued_log(issuer).read_text().splitlines()[-1])
    assert record["username"] == "caleb" and record["signing_key"] == public(signing)
    # Signed with that key and stored, the auditor signature verifies and freezes the plan.
    target = create(issuer)
    digest = pv.file_digest(pv.bundle_files(target.plan_dir))
    sig = base64.b64encode(signing.sign(sign_bytes("plan-bundle", {"plan_digest": digest})))
    status, _ = pv.write_signature(target, "auditor", sig, AUDITOR, canonical(certificate).decode())
    assert status == "ok" and pv.load_plan_view(target).frozen


def test_the_server_certifies_no_root_no_lab_key_and_no_junk(issuer, keys):
    for key in (
        public(keys["roots"]["auditor"]),
        public(keys["roots"]["lab"]),
        public(keys["lab"]),
    ):
        status, detail = pv.issue_certificate(issuer, "auditor", key, AUDITOR)
        assert status == "error" and "root or the lab" in detail
    for junk in ("", "not a key", public(keys["lab"]).rstrip("=")):
        assert pv.issue_certificate(issuer, "auditor", junk, AUDITOR)[0] == "error"
    assert pv.issue_certificate(issuer, "lab", browser_key(), LAB)[0] == "error"  # no lab root


def test_a_root_production_does_not_trust_certifies_nothing(issuer):
    wrong = replace(issuer, auditor_root_key=raw(Ed25519PrivateKey.generate()))
    assert "not the auditor root production trusts" in pv.issuer_problem(wrong, "auditor")
    assert not pv.server_certifies(wrong, "auditor")
    assert pv.issue_certificate(wrong, "auditor", browser_key(), AUDITOR)[0] == "error"
    assert "not a base64" in pv.issuer_problem(replace(issuer, auditor_root_key="x"), "auditor")


def test_the_certificate_route_is_json_and_own_party_only(issuer, monkeypatch):
    client = client_for(monkeypatch, issuer)
    body = {"party": "auditor", "signing_key": browser_key()}
    response = client.post("/sign/certificate", json=body)
    assert response.status_code == 200 and response.json()["certificate"]["role"] == "auditor"
    assert client.post("/sign/certificate", data=body).status_code == 415  # no form posts
    lab = client_for(monkeypatch, issuer, party="lab")
    assert lab.post("/sign/certificate", json=body).status_code == 403
    from server import app as web

    anonymous = TestClient(web.app, follow_redirects=False)
    assert anonymous.post("/sign/certificate", json=body).status_code == 401


def test_sign_buttons_ask_for_a_certificate_and_no_page_offers_a_new_root(issuer, monkeypatch):
    create(issuer)
    client = client_for(monkeypatch, issuer)
    assert 'data-certify="/sign/certificate"' in client.get("/a/auto/plan").text
    assert 'data-certify="/sign/certificate"' in client.get("/agreements").text
    html = client.get("/a/auto/sign").text
    assert 'data-certify="/sign/certificate"' in html and "can sign as both parties" in html
    assert 'class="browser-key"' not in html and 'id="key-form-auditor"' not in html
    other = browser_key()
    refused = client.post("/a/auto/sign/key", data={"party": "auditor", "public_key": other})
    assert "Unset AIAB_AUDITOR_ROOT_KEY" in unquote(refused.headers["location"])


def test_without_the_root_the_sign_button_is_browser_only(settings, monkeypatch):
    create(settings)
    html = client_for(monkeypatch, settings).get("/a/auto/plan").text
    assert "quick-sign" in html and "data-certify" not in html
