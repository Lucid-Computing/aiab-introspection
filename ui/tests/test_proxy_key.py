"""The Proxy key page (MAC-478): a signed-in person whose verified email is on
AIAB_PROXY_USERS gets a logging-proxy key from the proxy's /_signup."""

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from server import app as web
from server import proxykeys
from server.auth import Account
from server.config import load_settings

PROXY_ENV = ("AIAB_PROXY_USERS", "AIAB_SIGNUP_URL", "AIAB_SIGNUP_CODE", "UI_PROXY_RECORD_FILE")


@pytest.fixture
def env(monkeypatch):
    for name in PROXY_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_off_unless_users_url_and_code_are_all_set(env):
    assert not load_settings().proxy_keys_enabled
    env.setenv("AIAB_PROXY_USERS", "a@example.com")
    env.setenv("AIAB_SIGNUP_CODE", "code")
    assert not load_settings().proxy_keys_enabled  # no URL: no default, not even the test VM
    env.setenv("AIAB_PROXY_URL", "https://test-vm.example")  # the Logs page's proxy is not it
    assert load_settings().signup_url is None
    env.setenv("AIAB_SIGNUP_URL", "https://staging.example/")
    settings = load_settings()
    assert settings.proxy_keys_enabled and settings.signup_url == "https://staging.example"
    env.setenv("AIAB_PROXY_USERS", " , ")
    assert not load_settings().proxy_keys_enabled


def test_users_are_lower_cased_and_split_on_commas_or_spaces(env):
    env.setenv("AIAB_PROXY_USERS", "Ann@Example.com, bob@example.com  cy@example.com,")
    assert load_settings().proxy_users == {"ann@example.com", "bob@example.com", "cy@example.com"}


def test_the_code_is_not_in_the_settings_repr(env):
    env.setenv("AIAB_SIGNUP_CODE", "do-not-print-me")
    assert "do-not-print-me" not in repr(load_settings())


def test_the_record_file_sits_next_to_the_accounts_file(env, tmp_path):
    env.setenv("UI_ACCOUNTS_FILE", str(tmp_path / "accounts.json"))
    assert load_settings().proxy_record_file == tmp_path / "proxy-keys.jsonl"
    env.setenv("UI_PROXY_RECORD_FILE", str(tmp_path / "elsewhere.jsonl"))
    assert load_settings().proxy_record_file == Path(tmp_path / "elsewhere.jsonl")


# --------------------------------------------------------------------------- the page

PROXY = "https://proxy.example"
CODE = "signup-code-for-tests"
ALLOWED = "ann.lee@example.com"


class FakeProxy:
    """The logging proxy's /_signup: ``answers`` are replayed in order (default: a key)."""

    def __init__(self):
        self.asked = []
        self.answers = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url == f"{PROXY}/_signup" and request.method == "POST"
        self.asked.append(json.loads(request.content))
        answer = self.answers.pop(0) if self.answers else None
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, int):
            return httpx.Response(answer, json={"error": "nope"})
        return httpx.Response(200, json={"name": self.asked[-1]["name"], "key": "pk-secret-123"})


@pytest.fixture
def proxy(monkeypatch, tmp_path):
    fake = FakeProxy()
    monkeypatch.setattr(web, "proxy_transport", httpx.MockTransport(fake))
    monkeypatch.setattr(
        web,
        "settings",
        replace(
            load_settings(),
            proxy_users=frozenset({ALLOWED}),
            signup_url=PROXY,
            signup_code=CODE,
            proxy_record_file=tmp_path / "proxy-keys.jsonl",
            oidc_issuer="https://idp.example",  # proxy keys come with a provider sign-in
            oidc_client_id="client",
            oidc_client_secret="secret",
        ),
    )
    return fake


def client_as(email=ALLOWED):
    client = TestClient(web.app, follow_redirects=False)
    account = Account("ann", "auditor", "Ann", "unused")
    client.cookies.set(web.COOKIE, web.signer.issue(account, email))
    return client


def records():
    path = web.settings.proxy_record_file
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_logged_out_goes_to_login(proxy):
    client = TestClient(web.app, follow_redirects=False)
    assert client.get("/proxy").headers["location"] == "/login?next=/proxy"


def test_an_allowed_person_sees_the_form_with_a_suggested_name(proxy):
    html = client_as().get("/proxy").text
    assert 'method="post" action="/proxy/key"' in html and 'value="ann-lee"' in html
    assert 'href="/proxy"' in html  # the nav link, shown because the page is on


def test_get_a_key_shows_it_once_and_records_the_name_but_not_the_key(proxy, capsys):
    client = client_as()
    response = client.post("/proxy/key", data={"name": "ann"})
    assert response.status_code == 200
    assert proxy.asked == [{"name": "ann", "code": CODE}]
    assert "pk-secret-123" in response.text and response.headers["cache-control"] == "no-store"
    assert f"{PROXY}/_start#key=pk-secret-123" in response.text
    [row] = records()
    assert row["email"] == ALLOWED and row["name"] == "ann" and "pk-secret-123" not in str(row)
    again = client.get("/proxy").text
    assert "<code>ann</code>" in again and "pk-secret-123" not in again
    out = capsys.readouterr()
    assert "pk-secret-123" not in out.out + out.err and CODE not in out.out + out.err


def test_a_bad_name_is_refused_before_the_proxy_is_asked(proxy):
    for bad in ["", "has space", "dots.are.out", "x" * 33, "ann@example.com"]:
        response = client_as().post("/proxy/key", data={"name": bad})
        assert "letters, digits" in response.text
    assert proxy.asked == [] and records() == []


def test_a_taken_name_keeps_the_typed_name_and_another_one_works(proxy):
    proxy.answers = [409]
    client = client_as()
    response = client.post("/proxy/key", data={"name": "cam"})
    assert "name cam is taken" in response.text and 'value="cam"' in response.text
    assert records() == []
    assert "pk-secret-123" in client.post("/proxy/key", data={"name": "ann2"}).text
    assert [r["name"] for r in records()] == ["ann2"]


@pytest.mark.parametrize(
    ("answer", "says"),
    [
        (httpx.ReadTimeout("slow"), "took too long"),
        (httpx.ConnectError("down"), "could not be reached"),
        (httpx.ConnectTimeout("no answer"), "could not be reached"),  # nothing was made
        (401, "AIAB_SIGNUP_CODE"),
        (404, "AIAB_SIGNUP_CODE"),
        (500, "answered 500"),
    ],
)
def test_proxy_failures_are_explained_and_nothing_is_recorded(proxy, answer, says):
    proxy.answers = [answer]
    response = client_as().post("/proxy/key", data={"name": "ann"})
    assert response.status_code == 200 and says in response.text
    assert records() == []


@pytest.mark.parametrize(
    ("email", "says"),
    [
        ("someone@example.com", "not on the list"),
        (None, "no email"),  # a password account, or a login from before emails were kept
    ],
)
def test_only_allowed_verified_emails_get_a_key_even_posting_directly(proxy, email, says):
    client = client_as(email)
    assert says in client.get("/proxy").text
    response = client.post("/proxy/key", data={"name": "ann"})
    assert says in response.text and 'method="post" action="/proxy/key"' not in response.text
    assert proxy.asked == []


def test_off_when_not_configured(proxy, monkeypatch):
    monkeypatch.setattr(web, "settings", replace(web.settings, signup_code=None))
    client = client_as()
    page = client.get("/proxy").text
    assert "not set up" in page and 'href="/proxy"' not in page.split("<main")[0]
    assert "not set up" in client.post("/proxy/key", data={"name": "ann"}).text
    assert proxy.asked == []


@pytest.mark.parametrize(
    ("email", "name"),
    [
        ("ann.lee@example.com", "ann-lee"),
        ("o'brien+aiab@example.com", "o-brien-aiab"),
        ("..@example.com", "me"),
        ("a" * 40 + "@example.com", "a" * 32),
    ],
)
def test_suggested_names_follow_the_proxy_rule(email, name):
    assert proxykeys.suggest_name(email) == name
    assert proxykeys.NAME_RE.match(name)


# --------------------------------------------------------------------------- hardening


def test_the_signup_url_must_be_https_except_on_this_machine(env):
    for url, kept in [
        ("http://staging.example", None),
        ("ftp://staging.example", None),
        ("https://staging.example/", "https://staging.example"),
        ("http://localhost:8080", "http://localhost:8080"),
        ("http://localhost.evil.example", None),
        ("http://127.0.0.1.evil.example", None),
        ("https://", None),
        ("http://127.0.0.1:8080/", "http://127.0.0.1:8080"),
    ]:
        env.setenv("AIAB_SIGNUP_URL", url)
        assert load_settings().signup_url == kept


@pytest.mark.parametrize("body", [["not", "an", "object"], "just a string", {"key": ""}, {}])
def test_a_200_without_a_key_object_is_an_error_not_a_crash(proxy, monkeypatch, body):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    monkeypatch.setattr(web, "proxy_transport", transport)
    response = client_as().post("/proxy/key", data={"name": "ann"})
    assert response.status_code == 200 and "without a key" in response.text
    assert records() == []


def test_damaged_record_lines_do_not_break_the_page(proxy):
    path = web.settings.proxy_record_file
    good = {"email": ALLOWED, "name": "ann"}
    lines = ["not json", "[1, 2]", '"text"', json.dumps({"email": ALLOWED, "name": 7})]
    path.write_text("\n".join([*lines, json.dumps(good)]) + "\n")
    html = client_as().get("/proxy").text
    assert "<code>ann</code>" in html and "<code>7</code>" not in html


def test_the_record_file_is_owner_only(proxy):
    client_as().post("/proxy/key", data={"name": "ann"})
    assert web.settings.proxy_record_file.stat().st_mode & 0o777 == 0o600


def test_a_failed_record_still_shows_the_key(proxy, monkeypatch, capsys):
    def full_disk(*args):
        raise OSError("No space left on device")

    monkeypatch.setattr(proxykeys, "record", full_disk)
    response = client_as().post("/proxy/key", data={"name": "ann"})
    assert response.status_code == 200 and "pk-secret-123" in response.text
    out = capsys.readouterr().out
    assert "could not record proxy name ann" in out and "pk-secret-123" not in out


def test_without_a_provider_sign_in_the_page_says_so(proxy, monkeypatch):
    monkeypatch.setattr(web, "settings", replace(web.settings, oidc_issuer=None))
    assert "which this server doesn" in client_as(None).get("/proxy").text
