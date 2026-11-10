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
from aiab.baked.bundle import canonical, make_certificate
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from server import deploystatus, editing
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


def test_a_new_agreement_is_signed_by_the_lab_and_not_frozen(settings, keys):
    target = create(settings)
    lab_signature_verifies(target, keys)
    view = pv.load_plan_view(target)
    assert view.signatures["lab"]["status"] == "ok" and view.signatures["lab"]["auto"]
    assert not view.frozen
    history = pv.load_history(target.plan_dir)
    assert history[0]["user"] == "server" and "automatically" in history[0]["detail"]
    assert "caleb" in history[0]["detail"]


def test_every_save_is_signed_again_and_editing_stays_open(settings, keys):
    target = create(settings)
    first = lab_signature_verifies(target, keys)
    plan = json.loads((target.plan_dir / "plan.json").read_text())
    plan["description"] = "Does the agent leak credentials?"
    digest, issues = pv.save_plan(target, plan, AUDITOR)
    assert not issues
    second = lab_signature_verifies(target, keys)
    assert second != first
    plan["description"] = "Does the agent leak API keys?"
    digest2, issues = pv.save_plan(target, plan, AUDITOR)  # a second save is not refused
    assert not issues and digest2 != digest
    lab_signature_verifies(target, keys)


def test_the_auditor_signature_freezes_and_edit_brings_the_lab_signature_back(settings, keys):
    target = create(settings)
    sign_as_auditor(target, keys)
    view = pv.load_plan_view(target)
    assert view.frozen and view.signed_by_both
    assert pv.delete_signatures(target, AUDITOR) == 2
    assert pv.ensure_lab_signature(target, AUDITOR)
    view = pv.load_plan_view(target)
    assert view.signatures["lab"]["auto"] and not view.frozen
    assert view.signatures["auditor"]["status"] == "missing"


def test_an_uncertified_server_key_signs_nothing(settings, keys):
    # A key the certificate does not name.
    other = replace(settings, lab_signing_key=raw(Ed25519PrivateKey.generate()))
    assert "certifies another key" in pv.lab_auto_problem(other)
    target = create(other)
    assert not (target.plan_dir / "signatures/lab.sig").exists()
    view = pv.load_plan_view(target)
    assert view.signatures["lab"]["status"] == "missing" and not view.signatures["lab"]["auto"]
    bad = replace(settings, lab_signing_key="not a key")
    assert "not a base64" in pv.lab_auto_problem(bad)
    # No certificate at all, or one a stranger's root made.
    missing = replace(settings, lab_signing_certificate=None)
    assert "not set" in pv.lab_auto_problem(missing)
    forged = make_certificate(Ed25519PrivateKey.generate(), "lab", public(keys["lab"]))
    stranger = replace(settings, lab_signing_certificate=canonical(forged).decode())
    assert "not made by the lab root" in pv.lab_auto_problem(stranger)
    # The root itself, certified by itself: refused, the root never signs plans.
    as_root = make_certificate(keys["roots"]["lab"], "lab", public(keys["roots"]["lab"]))
    rooted = replace(
        settings,
        lab_signing_key=raw(keys["roots"]["lab"]),
        lab_signing_certificate=canonical(as_root).decode(),
    )
    assert "not made by the lab root" in pv.lab_auto_problem(rooted)


def test_without_the_key_nothing_is_signed_and_a_lab_signature_freezes(settings, keys):
    plain = replace(settings, lab_signing_key=None)
    assert pv.lab_auto_problem(plain) is None
    target = create(plain)
    assert not (target.plan_dir / "signatures").exists() or not any(
        (target.plan_dir / "signatures").iterdir()
    )
    digest = pv.file_digest(pv.bundle_files(target.plan_dir))
    sig = base64.b64encode(keys["lab"].sign(sign_bytes("plan-bundle", {"plan_digest": digest})))
    assert pv.write_signature(target, "lab", sig, LAB)[0] == "invalid"  # no certificate
    assert pv.write_signature(target, "lab", sig, LAB, cert_text(keys, "lab"))[0] == "ok"
    view = pv.load_plan_view(target)
    assert view.frozen and not view.signatures["lab"]["auto"]
    assert view.signatures["lab"]["signing_key"] == public(keys["lab"])
    # Removing the signature removes its certificate too.
    assert pv.remove_signature(target, "lab", LAB)
    assert not (target.plan_dir / "signatures/lab.cert").exists()


def test_one_signing_key_for_both_parties_is_refused_as_the_box_refuses_it(settings, keys):
    """The lab's certified key, certified again by the auditor root as the auditor's: the
    box refuses the pair (certified_keys), so the UI refuses to store it and never shows
    "signed by both"."""
    target = create(settings)  # the server's lab signature is there
    digest = pv.file_digest(pv.bundle_files(target.plan_dir))
    shared = make_certificate(keys["roots"]["auditor"], "auditor", public(keys["lab"]))
    sig = base64.b64encode(keys["lab"].sign(sign_bytes("plan-bundle", {"plan_digest": digest})))
    status, detail = pv.write_signature(target, "auditor", sig, AUDITOR, canonical(shared).decode())
    assert status == "invalid" and "must differ" in detail
    # Written behind the UI's back: the view marks both invalid rather than "signed by both".
    (target.plan_dir / "signatures/auditor.sig").write_text(sig.decode() + "\n")
    pv.write_certificate(target.plan_dir / "signatures/auditor.cert", shared)
    view = pv.load_plan_view(target)
    assert not view.signed_by_both
    assert {s["status"] for s in view.signatures.values()} == {"invalid"}


def test_a_rotated_lab_root_makes_the_server_sign_again_with_the_new_certificate(settings, keys):
    target = create(settings)
    old = (target.plan_dir / "signatures/lab.cert").read_text()
    new_root = Ed25519PrivateKey.generate()
    trust = json.loads(keys["trust"].read_text())
    trust["lab_key"] = public(new_root)
    keys["trust"].write_text(json.dumps(trust))
    fresh = make_certificate(new_root, "lab", public(keys["lab"]))
    rotated = replace(settings, lab_signing_certificate=canonical(fresh).decode())
    rotated = pv.for_agreement(rotated, "auto")
    assert pv.lab_auto_problem(rotated) is None
    # Same plan, same signing key, but the stored certificate is the old root's: re-sign.
    assert pv.ensure_lab_signature(rotated, AUDITOR)
    assert (target.plan_dir / "signatures/lab.cert").read_text() != old
    assert pv.load_plan_view(rotated).signatures["lab"]["status"] == "ok"


def client_for(monkeypatch, settings, party="auditor"):
    from server import app as web

    monkeypatch.setattr(web, "settings", settings)
    client = TestClient(web.app, follow_redirects=False)
    client.cookies.set(web.COOKIE, web.signer.issue(Account("caleb", party, "Caleb", "unused")))
    return client


def test_the_builder_can_save_twice_and_the_sign_screen_shows_the_badge(
    settings, keys, monkeypatch
):
    target = create(settings)
    client = client_for(monkeypatch, settings)
    editing.clear("auto")
    assert client.post("/a/auto/plan/edit", data={"next": "/a/auto/plan"}).status_code == 303
    lab_signature_verifies(target, keys)  # edit removed the signatures and the server re-signed
    for text in ("first edit", "second edit"):
        view = pv.load_plan_view(target)
        plan = json.loads((target.plan_dir / "plan.json").read_text())
        plan["description"] = text
        response = client.put("/a/auto/plan.json", json={"plan": plan, "version": view.digest})
        assert response.status_code == 200, response.text
        lab_signature_verifies(target, keys)
    html = client.get("/a/auto/sign").text
    assert "signed this version automatically by the server" in html
    assert "The lab signs on this server automatically" in html
    assert 'action="/a/auto/sign/remove"' not in html
    html = (
        client_for(
            monkeypatch, replace(settings, lab_signing_key=raw(Ed25519PrivateKey.generate()))
        )
        .get("/a/auto/sign")
        .text
    )
    assert "The server cannot sign as the lab" in html


# ---- AIAB_AUDITOR_KEY: the server signs as the auditor when an auditor presses Sign ----


@pytest.fixture
def both(settings, keys):
    return replace(
        settings,
        auditor_signing_key=raw(keys["auditor"]),
        auditor_signing_certificate=cert_text(keys, "auditor"),
    )


def test_the_auditor_is_never_signed_automatically(both, keys):
    target = create(both)
    view = pv.load_plan_view(target)
    assert view.signatures["auditor"]["status"] == "missing" and not view.frozen


def test_sign_on_the_server_as_the_auditor_freezes_the_plan(both, keys):
    target = create(both)
    digest = pv.file_digest(pv.bundle_files(target.plan_dir))
    assert pv.sign_on_server(target, "auditor", AUDITOR, digest) == ("ok", "signed")
    view = pv.load_plan_view(target)
    assert view.signatures["auditor"]["status"] == "ok" and not view.signatures["auditor"].get(
        "auto"
    )
    assert view.signatures["auditor"]["signing_key"] == public(keys["auditor"])
    assert view.frozen
    assert "caleb" == pv.load_history(target.plan_dir)[0]["user"]


def test_sign_on_the_server_refuses_a_changed_version_or_no_key(both, settings):
    target = create(both)
    status, detail = pv.sign_on_server(target, "auditor", AUDITOR, "sha256:" + "0" * 64)
    assert status == "error" and "changed" in detail
    status, detail = pv.sign_on_server(pv.for_agreement(settings, "auto"), "auditor", AUDITOR)
    assert status == "error" and "holds no auditor key" in detail
    wrong = replace(both, auditor_signing_certificate=cert_text_for_lab(both))
    assert "not made by the auditor root" in pv.server_problem(wrong, "auditor")


def cert_text_for_lab(settings):
    return settings.lab_signing_certificate


def test_the_sign_button_signs_on_the_server(both, keys, monkeypatch):
    create(both)
    client = client_for(monkeypatch, both)
    target = pv.for_agreement(both, "auto")
    digest = pv.file_digest(pv.bundle_files(target.plan_dir))
    html = client.get("/a/auto/plan").text
    assert 'action="/a/auto/sign/server"' in html and "quick-sign" not in html
    assert 'action="/a/auto/sign/server"' in client.get("/agreements").text
    assert "can sign as both parties" in client.get("/a/auto/sign").text
    response = client.post(
        "/a/auto/sign/server",
        data={"party": "auditor", "digest": digest, "next": "/a/auto/plan"},
    )
    assert response.status_code == 303 and response.headers["location"].startswith("/a/auto/plan")
    assert pv.load_plan_view(target).signatures["auditor"]["status"] == "ok"
    # The lab cannot use the auditor's key.
    lab = client_for(monkeypatch, both, party="lab")
    refused = lab.post("/a/auto/sign/server", data={"party": "auditor", "digest": digest})
    assert "own party" in unquote(refused.headers["location"])


def test_without_the_auditor_key_the_sign_button_signs_in_the_browser(settings, monkeypatch):
    create(settings)
    html = client_for(monkeypatch, settings).get("/a/auto/plan").text
    assert "quick-sign" in html and "/sign/server" not in html


def test_while_the_server_signs_no_page_offers_a_new_auditor_root(both, monkeypatch):
    create(both)
    client = client_for(monkeypatch, both)
    html = client.get("/a/auto/sign").text
    assert 'class="browser-key"' not in html and 'id="key-form-auditor"' not in html
    assert "Paste a signature made elsewhere" in html
    other = public(Ed25519PrivateKey.generate())
    refused = client.post("/a/auto/sign/key", data={"party": "auditor", "public_key": other})
    assert "Unset AIAB_AUDITOR_KEY" in unquote(refused.headers["location"])
