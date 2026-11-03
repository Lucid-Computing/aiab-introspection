"""OpenID Connect login: the browser goes to the provider and comes back with a code; the
server exchanges it, checks the ID token and issues its own cookie; an optional provider group gates
access and parties.json names the party."""

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
            "groups": ["Everyone", "aiab"],
        }
        claims.update(self.claims)
        claims.update(override)
        claims = {k: v for k, v in claims.items() if v is not None}  # None: leave it out
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
def okta(keypair, monkeypatch, tmp_path):
    private, jwk = keypair
    fake = FakeOkta(private, jwk)
    parties = tmp_path / "parties.json"
    auth.save_party(parties, "Teddy@lucid.example", "lab")  # case does not matter
    auth.save_party(parties, "caleb@auditor.example", "auditor")
    login = auth.OidcLogin(
        ISSUER,
        CLIENT_ID,
        CLIENT_SECRET,
        parties_file=parties,
        group="aiab",  # the Okta shape: a group in the ID token, fetched with its scope
        scopes="openid profile email groups",
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
            parties_file=parties,
            accounts_file=tmp_path / "accounts.json",
        ),
    )
    auth.save_account(  # a local account the operator made on this side, next to Okta
        web.settings.accounts_file,
        auth.Account("demo", "auditor", "Demo", auth.hash_password("demo-pass-1234")),
    )
    fake.parties = parties
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
    assert ("groups" in q["scope"].split()) == bool(web.oidc.group)  # scope follows the setting
    okta.nonce = q["nonce"]
    return q


def test_login_page_offers_the_provider_and_the_password_form(okta, client):
    html = client.get("/login?next=/runs").text
    assert 'href="/login/start?next=/runs"' in html and 'name="password"' in html
    assert "Sign in with WorkOS" in html


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


def test_the_parties_file_names_the_auditor(okta, client):
    okta.claims = {"preferred_username": "caleb@auditor.example", "email": "caleb@auditor.example"}
    q = start(client, okta)
    response = client.get("/login/callback", params={"code": "good-code", "state": q["state"]})
    assert web.signer.read(response.cookies[web.COOKIE])["party"] == "auditor"
    assert web.signer.read(response.cookies[web.COOKIE])["username"] == "caleb"


def test_not_in_the_group_or_not_in_the_parties_file_is_refused(okta, client):
    okta.claims = {"groups": ["Everyone"]}
    q = start(client, okta)
    response = client.get("/login/callback", params={"code": "good-code", "state": q["state"]})
    assert response.status_code == 200 and web.COOKIE not in response.cookies
    assert "not in the aiab group" in response.text
    okta.claims = {
        "email": "stranger@lucid.example",
        "preferred_username": "stranger@lucid.example",
    }
    q = start(client, okta)
    response = client.get("/login/callback", params={"code": "good-code", "state": q["state"]})
    assert response.status_code == 200 and web.COOKIE not in response.cookies
    assert "party stranger@lucid.example lab|auditor" in response.text
    # The operator adds them; the next login works without a restart.
    auth.save_party(okta.parties, "stranger@lucid.example", "lab")
    q = start(client, okta)
    response = client.get("/login/callback", params={"code": "good-code", "state": q["state"]})
    assert response.status_code == 303


def test_no_group_setting_means_the_parties_file_alone_gates(okta, client):
    """The WorkOS shape: no groups claim in the ID token at all, and no groups scope."""
    web.oidc.group = None
    web.oidc.scopes = "openid profile email"
    okta.claims = {"groups": None}
    q = start(client, okta)
    response = client.get("/login/callback", params={"code": "good-code", "state": q["state"]})
    assert response.status_code == 303
    assert web.signer.read(response.cookies[web.COOKIE])["party"] == "lab"
    auth.save_party(web.settings.parties_file, "teddy@lucid.example", None)  # dropped
    q = start(client, okta)
    response = client.get("/login/callback", params={"code": "good-code", "state": q["state"]})
    assert response.status_code == 200 and "has no party yet" in response.text


def test_defaults_are_the_workos_shape(monkeypatch):
    for key in ("OIDC_GROUP", "OIDC_SCOPES", "OIDC_PROVIDER_NAME"):
        monkeypatch.delenv(key, raising=False)
    settings = load_settings()
    assert settings.oidc_group is None and settings.oidc_scopes == "openid profile email"
    assert settings.oidc_provider_name == "WorkOS"


def test_party_file_and_cli(tmp_path, monkeypatch):
    path = tmp_path / "parties.json"
    auth.save_party(path, "A@x.example", "lab")
    auth.save_party(path, "b@x.example", "lab")
    auth.save_party(path, "b@x.example", "auditor")  # moves, never in both
    assert auth.load_parties(path) == {"a@x.example": "lab", "b@x.example": "auditor"}
    assert json.loads(path.read_text()) == {"lab": ["a@x.example"], "auditor": ["b@x.example"]}
    auth.save_party(path, "a@x.example", None)
    assert auth.load_parties(path) == {"b@x.example": "auditor"}
    with pytest.raises(ValueError):
        auth.save_party(path, "c@x.example", "owner")
    assert auth.main(["party", "c@x.example", "lab", "--file", str(path)]) == 0
    assert auth.main(["party", "b@x.example", "none", "--file", str(path)]) == 0
    assert auth.load_parties(path) == {"c@x.example": "lab"}


def test_wrong_state_missing_cookie_and_provider_errors_are_refused(okta, client):
    q = start(client, okta)
    wrong = client.get("/login/callback", params={"code": "good-code", "state": "someone-elses"})
    assert wrong.status_code == 200 and "not the one this browser started" in wrong.text
    refused = client.get(
        "/login/callback",
        params={"state": q["state"], "error": "access_denied", "error_description": "no app"},
    )
    assert refused.status_code == 200 and "WorkOS refused: no app" in refused.text
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


def test_local_accounts_work_next_to_okta(okta, client):
    bad = client.post("/login", data={"username": "demo", "password": "whatever-1234"})
    assert bad.status_code == 200 and web.COOKIE not in bad.cookies
    good = client.post("/login", data={"username": "demo", "password": "demo-pass-1234"})
    assert good.status_code == 303
    assert web.signer.read(good.cookies[web.COOKIE])["party"] == "auditor"
    assert client.post("/logout").headers["location"] == "/login"  # no Okta session to end


def test_logout_ends_the_provider_session_too_when_it_has_an_end_session_endpoint(okta, client):
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


def test_discovery_must_name_the_same_issuer(keypair, tmp_path):
    private, jwk = keypair

    def lying(request):
        return httpx.Response(200, json={"issuer": "https://other.example"})

    login = auth.OidcLogin(
        ISSUER,
        CLIENT_ID,
        CLIENT_SECRET,
        parties_file=tmp_path / "p.json",
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


def test_bootstrap_adds_new_accounts_and_keeps_existing_passwords(tmp_path, monkeypatch, capsys):
    path = tmp_path / "accounts.json"
    auth.save_account(path, auth.Account("lab@lab.com", "lab", "Lab", auth.hash_password("kept-1")))
    entries = "lab@lab.com:lab:lab123,auditor@auditor.com:auditor:auditor123,x:lab:short"
    monkeypatch.setenv("UI_BOOTSTRAP_ACCOUNTS", entries)
    assert auth.main(["bootstrap", "--file", str(path)]) == 0
    assert auth.authenticate(path, "lab@lab.com", "kept-1") is not None  # not overwritten
    assert auth.authenticate(path, "auditor@auditor.com", "auditor123").party == "auditor"
    assert "skipping x" in capsys.readouterr().err
