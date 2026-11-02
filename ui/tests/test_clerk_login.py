"""Clerk login: the page hands over a session token, the server checks it and issues its
own cookie; the party comes from Clerk's public_metadata."""

import base64
import json
import time
from dataclasses import replace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from server import app as web
from server import auth
from server.config import load_settings

FRONTEND_API = "clerk.example.test"
PUBLISHABLE = "pk_test_" + base64.b64encode(f"{FRONTEND_API}$".encode()).decode().rstrip("=")
SECRET = "sk_test_unused"
ORIGIN = "http://testserver"


@pytest.fixture(scope="module")
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    jwk.update(kid="key-1", use="sig", alg="RS256")
    return private, public_pem, jwk


def clerk_user(party="lab", **extra):
    user = {
        "id": "user_1",
        "username": "teddy",
        "first_name": "Teddy",
        "last_name": "Lab",
        "primary_email_address_id": "idn_1",
        "email_addresses": [{"id": "idn_1", "email_address": "teddy@example.test"}],
        "public_metadata": {"party": party} if party else {},
    }
    user.update(extra)
    return user


class FakeClerk:
    """The two Clerk endpoints the login touches, plus the two the CLI touches."""

    def __init__(self, jwk, user):
        self.jwk, self.user, self.calls, self.patched = jwk, user, [], None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.url.path == "/.well-known/jwks.json":
            return httpx.Response(200, json={"keys": [self.jwk]})
        if request.url.path == "/v1/users/user_1":
            return httpx.Response(200, json=self.user)
        if request.url.path == "/v1/users":
            email = request.url.params.get("email_address")
            return httpx.Response(200, json=[self.user] if email == "teddy@example.test" else [])
        if request.url.path == "/v1/users/user_1/metadata":
            self.patched = json.loads(request.content)
            return httpx.Response(200, json=self.user)
        return httpx.Response(404)


def make_token(private, *, kid="key-1", azp=ORIGIN, exp_in=60, sub="user_1"):
    now = int(time.time())
    claims = {"sub": sub, "sid": "sess_1", "iat": now, "nbf": now - 5, "exp": now + exp_in}
    if azp:
        claims["azp"] = azp
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def clerk(keypair, monkeypatch):
    _, _, jwk = keypair
    fake = FakeClerk(jwk, clerk_user())
    login = auth.ClerkLogin(PUBLISHABLE, SECRET, transport=httpx.MockTransport(fake))
    monkeypatch.setattr(web, "clerk", login)
    monkeypatch.setattr(
        web,
        "settings",
        replace(load_settings(), clerk_publishable_key=PUBLISHABLE, clerk_secret_key=SECRET),
    )
    return fake


@pytest.fixture
def client():
    return TestClient(web.app, follow_redirects=False)


def test_frontend_api_is_decoded_from_the_publishable_key():
    assert auth.clerk_frontend_api(PUBLISHABLE) == FRONTEND_API
    with pytest.raises(ValueError):
        auth.clerk_frontend_api("pk_test_" + base64.b64encode(b"no-dollar").decode())
    with pytest.raises(ValueError):
        auth.clerk_frontend_api("not-a-key")


def test_login_page_carries_clerk_and_no_password_form(clerk, client):
    html = client.get("/login").text
    assert f"https://{FRONTEND_API}/npm/@clerk/clerk-js@6" in html
    assert f'data-clerk-publishable-key="{PUBLISHABLE}"' in html
    assert 'name="password"' not in html


def test_valid_token_becomes_the_ui_cookie_with_the_party(keypair, clerk, client):
    private, _, _ = keypair
    response = client.post(
        "/login",
        data={"token": make_token(private), "next": "/a/bio/plan"},
        headers={"origin": ORIGIN},
    )
    assert response.status_code == 303 and response.headers["location"] == "/a/bio/plan"
    user = web.signer.read(response.cookies[web.COOKIE])
    assert user == {"username": "teddy", "party": "lab", "display_name": "Teddy Lab"}
    # The key set: Frontend API, no secret. The user: Backend API, with the secret.
    assert ("GET", "/.well-known/jwks.json", None) in clerk.calls
    assert ("GET", "/v1/users/user_1", f"Bearer {SECRET}") in clerk.calls


def test_user_without_a_party_is_refused_and_told_how_to_get_one(keypair, clerk, client):
    private, _, _ = keypair
    clerk.user = clerk_user(party=None)
    response = client.post(
        "/login", data={"token": make_token(private)}, headers={"origin": ORIGIN}
    )
    assert response.status_code == 200 and web.COOKIE not in response.cookies
    assert "set-party teddy@example.test" in response.text
    assert "Sign out of Clerk" in response.text  # the page must not retry by itself


def test_tokens_for_another_origin_unknown_key_or_expired_are_refused(keypair, clerk, client):
    private, _, _ = keypair
    for token, reason in [
        (make_token(private, azp="https://other.example"), "issued to https://other.example"),
        (make_token(private, kid="key-2"), "does not know"),
        (make_token(private, exp_in=-60), "expired"),
    ]:
        response = client.post("/login", data={"token": token}, headers={"origin": ORIGIN})
        assert response.status_code == 200 and web.COOKIE not in response.cookies, reason
        assert reason in response.text


def test_token_without_azp_is_accepted_and_the_forwarded_origin_counts(keypair, clerk, client):
    private, _, _ = keypair
    no_azp = client.post("/login", data={"token": make_token(private, azp=None)})
    assert no_azp.status_code == 303
    behind_proxy = client.post(
        "/login",
        data={"token": make_token(private, azp="https://ui.example.test")},
        headers={"x-forwarded-proto": "https", "host": "ui.example.test"},
    )
    assert behind_proxy.status_code == 303


def test_password_form_is_refused_while_clerk_is_on(clerk, client):
    response = client.post("/login", data={"username": "teddy", "password": "whatever-1234"})
    assert response.status_code == 200 and web.COOKIE not in response.cookies
    assert "Sign in with Clerk" in response.text


def test_logout_asks_the_page_to_end_the_clerk_session_too(clerk, client):
    response = client.post("/logout")
    assert response.status_code == 303 and response.headers["location"] == "/login?signed_out=1"


def test_without_clerk_the_local_accounts_still_work(tmp_path, monkeypatch, client):
    monkeypatch.setattr(web, "clerk", None)
    monkeypatch.setattr(
        web, "settings", replace(load_settings(), accounts_file=tmp_path / "accounts.json")
    )
    auth.save_account(
        web.settings.accounts_file,
        auth.Account("caleb", "auditor", "Caleb", auth.hash_password("auditor-pass-1")),
    )
    assert 'name="password"' in client.get("/login").text
    bad = client.post("/login", data={"username": "caleb", "password": "wrong-pass-1"})
    assert bad.status_code == 200 and web.COOKIE not in bad.cookies
    good = client.post("/login", data={"username": "caleb", "password": "auditor-pass-1"})
    assert good.status_code == 303
    assert web.signer.read(good.cookies[web.COOKIE])["party"] == "auditor"


def test_set_party_finds_the_user_by_email_and_patches_public_metadata(keypair):
    _, _, jwk = keypair
    fake = FakeClerk(jwk, clerk_user(party=None))
    transport = httpx.MockTransport(fake)
    assert auth.set_party(SECRET, "teddy@example.test", "auditor", transport=transport) == "user_1"
    assert fake.patched == {"public_metadata": {"party": "auditor"}}
    with pytest.raises(LookupError):
        auth.set_party(SECRET, "nobody@example.test", "lab", transport=transport)
    with pytest.raises(ValueError):
        auth.set_party(SECRET, "teddy@example.test", "owner", transport=transport)
