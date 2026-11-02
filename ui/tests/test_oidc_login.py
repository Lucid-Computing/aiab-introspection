"""OpenID Connect login: the browser goes to the provider and comes back with a code; the
server exchanges it, checks the ID token and issues its own cookie; the party comes from
the groups claim."""

import json
import time
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from server import app as web
from server import auth
from server.config import load_settings

ISSUER = "https://lucid.okta.example"
CLIENT_ID = "0oa-test-client"
CLIENT_SECRET = "client-secret-unused"
ORIGIN = "http://testserver"
REDIRECT = ORIGIN + "/login/callback"


@pytest.fixture(scope="module")
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    jwk.update(kid="okta-key-1", use="sig", alg="RS256")
    return private, jwk


class FakeOkta:
    """Discovery, the key set and the token endpoint. ``claims`` is what the next ID token
    carries; ``issued`` records what the token endpoint was asked."""

    def __init__(self, private, jwk):
        self.private, self.jwk = private, jwk
        self.claims = {}
        self.issued = []
        self.nonce = None  # set by the test from the authorize request

    def id_token(self, **override):
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": "00u-teddy",
            "iat": now,
            "exp": now + 300,
            "nonce": self.nonce,
            "preferred_username": "teddy@lucid.example",
            "email": "teddy@lucid.example",
            "name": "Teddy Lab",
            "groups": ["Everyone", "aiab-lab"],
        }
        claims.update(self.claims)
        claims.update(override)
        return jwt.encode(claims, self.private, algorithm="RS256", headers={"kid": "okta-key-1"})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/oauth2/v1/authorize",
                    "token_endpoint": f"{ISSUER}/oauth2/v1/token",
                    "jwks_uri": f"{ISSUER}/oauth2/v1/keys",
                    "end_session_endpoint": f"{ISSUER}/oauth2/v1/logout",
                },
            )
        if path == "/oauth2/v1/keys":
            return httpx.Response(200, json={"keys": [self.jwk]})
        if path == "/oauth2/v1/token":
            form = parse_qs(request.content.decode())
            self.issued.append(
                {k: v[0] for k, v in form.items()} | {"auth": request.headers.get("authorization")}
            )
            if form["code"][0] != "good-code":
                return httpx.Response(
                    400, json={"error": "invalid_grant", "error_description": "bad code"}
                )
            return httpx.Response(
                200,
                json={"id_token": self.id_token(), "access_token": "at", "token_type": "Bearer"},
            )
        return httpx.Response(404)


@pytest.fixture
def okta(keypair, monkeypatch):
    private, jwk = keypair
    fake = FakeOkta(private, jwk)
    login = auth.OidcLogin(
        ISSUER,
        CLIENT_ID,
        CLIENT_SECRET,
        group_lab="aiab-lab",
        group_auditor="aiab-auditor",
        transport=httpx.MockTransport(fake),
    )
    monkeypatch.setattr(web, "oidc", login)
    monkeypatch.setattr(
        web,
        "settings",
        replace(
            load_settings(),
            oidc_issuer=ISSUER,
            oidc_client_id=CLIENT_ID,
            oidc_client_secret=CLIENT_SECRET,
        ),
    )
    return fake


@pytest.fixture
def client():
    return TestClient(web.app, follow_redirects=False)


def start(client, okta, next_path="/a/bio/plan"):
    """GET /login/start: returns the state the provider must echo and the callback params."""
    response = client.get("/login/start", params={"next": next_path})
    assert response.status_code == 303
    url = urlsplit(response.headers["location"])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f"{url.scheme}://{url.netloc}{url.path}" == f"{ISSUER}/oauth2/v1/authorize"
    assert q["response_type"] == "code" and q["client_id"] == CLIENT_ID
    assert q["redirect_uri"] == REDIRECT and q["code_challenge_method"] == "S256"
    assert "groups" in q["scope"].split()
    okta.nonce = q["nonce"]
    return q


def test_login_page_offers_okta_and_no_password_form(okta, client):
    html = client.get("/login?next=/runs").text
    assert 'href="/login/start?next=/runs"' in html and 'name="password"' not in html


def test_the_whole_flow_issues_the_ui_cookie_with_the_party_from_the_group(okta, client):
    q = start(client, okta)
    response = client.get("/login/callback", params={"code": "good-code", "state": q["state"]})
    assert response.status_code == 303 and response.headers["location"] == "/a/bio/plan"
    user = web.signer.read(response.cookies[web.COOKIE])
    assert user == {"username": "teddy", "party": "lab", "display_name": "Teddy Lab"}
    assert response.cookies.get(web.ID_TOKEN_COOKIE)
    # The code went to the token endpoint with PKCE and the client secret, not in the URL.
    [asked] = okta.issued
    assert asked["grant_type"] == "authorization_code" and asked["redirect_uri"] == REDIRECT
    assert asked["code_verifier"] and asked["auth"].startswith("Basic ")


def test_auditor_group_gives_the_auditor_party(okta, client):
    okta.claims = {"groups": ["aiab-auditor"], "preferred_username": "caleb@auditor.example"}
    q = start(client, okta)
    response = client.get("/login/callback", params={"code": "good-code", "state": q["state"]})
    assert web.signer.read(response.cookies[web.COOKIE])["party"] == "auditor"
    assert web.signer.read(response.cookies[web.COOKIE])["username"] == "caleb"


@pytest.mark.parametrize(
    "groups, reason",
    [([], "neither party group"), (["aiab-lab", "aiab-auditor"], "both party groups")],
)
def test_no_group_or_both_groups_is_refused(okta, client, groups, reason):
    okta.claims = {"groups": groups}
    q = start(client, okta)
    response = client.get("/login/callback", params={"code": "good-code", "state": q["state"]})
    assert response.status_code == 200 and web.COOKIE not in response.cookies
    assert reason in response.text


def test_wrong_state_missing_cookie_and_provider_errors_are_refused(okta, client):
    q = start(client, okta)
    wrong = client.get("/login/callback", params={"code": "good-code", "state": "someone-elses"})
    assert wrong.status_code == 200 and "not the one this browser started" in wrong.text
    refused = client.get(
        "/login/callback",
        params={"state": q["state"], "error": "access_denied", "error_description": "no app"},
    )
    assert refused.status_code == 200 and "Okta refused: no app" in refused.text
    bad_code = client.get("/login/callback", params={"code": "bad-code", "state": q["state"]})
    assert bad_code.status_code == 200 and "bad code" in bad_code.text
    fresh = TestClient(web.app, follow_redirects=False)  # no login cookie at all
    assert (
        "not the one this browser started"
        in fresh.get("/login/callback", params={"code": "good-code", "state": q["state"]}).text
    )


def test_id_token_for_another_client_issuer_nonce_or_expired_is_refused(okta, client):
    login = web.oidc
    q = start(client, okta)
    for override, reason in [
        ({"aud": "other-client"}, "did not verify"),
        ({"iss": "https://evil.example"}, "did not verify"),
        ({"exp": int(time.time()) - 60}, "did not verify"),
        ({"nonce": "other"}, "not belong to this login attempt"),
    ]:
        with pytest.raises(auth.LoginError, match=reason):
            login.claims(okta.id_token(**override), q["nonce"])
    assert login.claims(okta.id_token(), q["nonce"])["sub"] == "00u-teddy"


def test_password_form_is_refused_while_okta_is_on(okta, client):
    response = client.post("/login", data={"username": "teddy", "password": "whatever-1234"})
    assert response.status_code == 200 and web.COOKIE not in response.cookies
    assert "Sign in through Okta" in response.text


def test_logout_ends_the_okta_session_too(okta, client):
    q = start(client, okta)
    client.get("/login/callback", params={"code": "good-code", "state": q["state"]})
    response = client.post("/logout")
    assert response.status_code == 303
    url = urlsplit(response.headers["location"])
    assert url.path == "/oauth2/v1/logout"
    out = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert out["post_logout_redirect_uri"] == ORIGIN + "/login" and out["id_token_hint"]


def test_redirect_uri_follows_the_proxy_headers(okta, client):
    response = client.get(
        "/login/start", headers={"x-forwarded-proto": "https", "host": "aiab-ui-dev.onrender.com"}
    )
    q = {k: v[0] for k, v in parse_qs(urlsplit(response.headers["location"]).query).items()}
    assert q["redirect_uri"] == "https://aiab-ui-dev.onrender.com/login/callback"


def test_discovery_must_name_the_same_issuer(keypair):
    private, jwk = keypair

    def lying(request):
        return httpx.Response(200, json={"issuer": "https://other.example"})

    login = auth.OidcLogin(
        ISSUER,
        CLIENT_ID,
        CLIENT_SECRET,
        group_lab="a",
        group_auditor="b",
        transport=httpx.MockTransport(lying),
    )
    with pytest.raises(auth.LoginError, match="calls itself"):
        login.metadata()


def test_without_okta_the_local_accounts_still_work(tmp_path, monkeypatch, client):
    monkeypatch.setattr(web, "oidc", None)
    monkeypatch.setattr(  # whatever ui/.env holds, this test is the no-Okta case
        web,
        "settings",
        replace(
            load_settings(),
            accounts_file=tmp_path / "accounts.json",
            oidc_issuer=None,
            oidc_client_id=None,
            oidc_client_secret=None,
        ),
    )
    auth.save_account(
        web.settings.accounts_file,
        auth.Account("caleb", "auditor", "Caleb", auth.hash_password("auditor-pass-1")),
    )
    assert 'name="password"' in client.get("/login").text
    assert client.get("/login/start").status_code == 303  # back to /login with an error
    bad = client.post("/login", data={"username": "caleb", "password": "wrong-pass-1"})
    assert bad.status_code == 200 and web.COOKIE not in bad.cookies
    good = client.post("/login", data={"username": "caleb", "password": "auditor-pass-1"})
    assert good.status_code == 303
    assert web.signer.read(good.cookies[web.COOKIE])["party"] == "auditor"
    assert client.post("/logout").headers["location"] == "/login"
