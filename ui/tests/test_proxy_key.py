"""The Proxy key page (MAC-478): a signed-in person whose verified email is on
AIAB_PROXY_USERS gets a logging-proxy key from the proxy's /_signup."""

from pathlib import Path

import pytest

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
