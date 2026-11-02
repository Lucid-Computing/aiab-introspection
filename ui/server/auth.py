"""Who is logged in, and which party they are: Clerk login, the signed session cookie, and
the local accounts the UI used before Clerk.

Clerk (clerk.com) authenticates; the UI's own cookie authorises. The login page runs
Clerk's sign-in and hands the resulting session token to ``POST /login``; ``ClerkLogin``
checks its signature against the instance's key set, reads the user from Clerk's Backend
API, and the app issues the same ``SessionSigner`` cookie as before, carrying the party.
The party is the user's ``public_metadata.party`` in Clerk (``lab`` or ``auditor``); a user
without one cannot log in. ``python -m server.auth set-party EMAIL PARTY`` sets it.

Without Clerk keys the previous system stays: local accounts with hashed passwords in
``accounts.json`` (``python -m server.auth add USERNAME PARTY``). That path is for
laptops and for rollback; see docs/CLERK_AUTH_MIGRATION.md.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx
import jwt

PBKDF2_ITERATIONS = 600_000
PARTIES = ("lab", "auditor")
SESSION_TTL_SECONDS = 12 * 3600


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${_b64(salt)}${_b64(derived)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        derived = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), _unb64(salt), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived, _unb64(expected))


@dataclass(frozen=True)
class Account:
    username: str
    party: str
    display_name: str
    password_hash: str


def load_accounts(path: Path) -> dict[str, Account]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {row["username"]: Account(**row) for row in data.get("accounts", [])}


def save_account(path: Path, account: Account) -> None:
    accounts = load_accounts(path)
    accounts[account.username] = account
    payload = {"accounts": [asdict(a) for a in accounts.values()]}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2) + "\n")


def authenticate(path: Path, username: str, password: str) -> Account | None:
    account = load_accounts(path).get(username)
    if account is None or not verify_password(password, account.password_hash):
        return None
    return account


class SessionSigner:
    """HMAC-signed cookie: payload.signature, no server-side store."""

    def __init__(self, secret: bytes, ttl: int = SESSION_TTL_SECONDS) -> None:
        self._secret = secret
        self._ttl = ttl

    def _mac(self, payload: str) -> str:
        return _b64(hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).digest())

    def issue(self, account: Account) -> str:
        body = {
            "u": account.username,
            "p": account.party,
            "n": account.display_name,
            "exp": int(time.time()) + self._ttl,
        }
        payload = _b64(json.dumps(body, separators=(",", ":")).encode("utf-8"))
        return f"{payload}.{self._mac(payload)}"

    def read(self, token: str | None) -> dict[str, str] | None:
        if not token or "." not in token:
            return None
        payload, mac = token.rsplit(".", 1)
        if not hmac.compare_digest(mac, self._mac(payload)):
            return None
        try:
            body = json.loads(_unb64(payload))
        except ValueError:
            return None
        if body.get("exp", 0) < time.time() or body.get("p") not in PARTIES:
            return None
        return {"username": body["u"], "party": body["p"], "display_name": body.get("n", body["u"])}


# ------------------------------------------------------------------------------ Clerk

CLERK_API = "https://api.clerk.com/v1"
JWKS_MIN_REFETCH_SECONDS = 60  # an unknown key id refetches the key set, at most this often


class ClerkLoginError(Exception):
    """The token or the user behind it is not acceptable; the message is shown to the user."""


def clerk_frontend_api(publishable_key: str) -> str:
    """The instance's Frontend API host: the publishable key is ``pk_<env>_<base64(host$)>``."""
    try:
        encoded = publishable_key.split("_", 2)[2]
        host = base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode("ascii")
    except (IndexError, ValueError, UnicodeDecodeError) as exc:
        raise ValueError("CLERK_PUBLISHABLE_KEY is not a Clerk publishable key") from exc
    if not host.endswith("$"):
        raise ValueError("CLERK_PUBLISHABLE_KEY is not a Clerk publishable key")
    return host[:-1]


class ClerkLogin:
    """Turns a Clerk session token into an Account, or raises ClerkLoginError.

    The key set comes from the Frontend API (public, no secret needed); the user comes from
    the Backend API with the secret key. ``transport`` is for tests.
    """

    def __init__(
        self,
        publishable_key: str,
        secret_key: str,
        *,
        transport: Any | None = None,
        leeway: int = 10,
    ) -> None:
        self._frontend_api = clerk_frontend_api(publishable_key)
        self._secret = secret_key
        self._leeway = leeway
        self._keys: dict[str, Any] = {}  # kid -> public key
        self._fetched_at = 0.0
        self._lock = threading.Lock()
        self._transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=10, transport=self._transport)

    def _load_keys(self) -> None:
        with self._client() as client:
            response = client.get(f"https://{self._frontend_api}/.well-known/jwks.json")
            response.raise_for_status()
        keys = {}
        for jwk in response.json().get("keys", []):
            if jwk.get("kid") and jwk.get("kty") == "RSA":
                keys[jwk["kid"]] = jwt.PyJWK(jwk).key
        self._keys, self._fetched_at = keys, time.time()

    def _key_for(self, token: str) -> Any:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError as exc:
            raise ClerkLoginError("The sign-in token is not readable. Try again.") from exc
        with self._lock:
            if kid not in self._keys and time.time() - self._fetched_at > JWKS_MIN_REFETCH_SECONDS:
                try:
                    self._load_keys()
                except (httpx.HTTPError, ValueError) as exc:
                    raise ClerkLoginError("Clerk did not answer; try again in a moment.") from exc
            key = self._keys.get(kid)
        if key is None:
            raise ClerkLoginError("The sign-in token was signed with a key this UI does not know.")
        return key

    def claims(self, token: str, origins: set[str]) -> dict[str, Any]:
        """The verified claims. ``azp``, when present, must be one of ``origins``: the page
        that obtained the token must be this UI, not another site the user is signed in to."""
        key = self._key_for(token)
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                leeway=self._leeway,
                options={"require": ["exp", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise ClerkLoginError("The sign-in token has expired. Try again.") from exc
        except jwt.PyJWTError as exc:
            raise ClerkLoginError("The sign-in token did not verify.") from exc
        azp = claims.get("azp")
        if azp and azp not in origins:
            raise ClerkLoginError(f"The sign-in token was issued to {azp}, not to this UI.")
        return claims

    def user(self, user_id: str) -> Account:
        """The Clerk user as an Account: username (or primary email), party from
        ``public_metadata.party``, display name from the profile."""
        try:
            with self._client() as client:
                response = client.get(
                    f"{CLERK_API}/users/{user_id}",
                    headers={"Authorization": f"Bearer {self._secret}"},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ClerkLoginError("Clerk did not answer; try again in a moment.") from exc
        return account_from_clerk(response.json())

    def login(self, token: str, origins: set[str]) -> Account:
        return self.user(self.claims(token, origins)["sub"])


def primary_email(user: dict[str, Any]) -> str | None:
    wanted = user.get("primary_email_address_id")
    for entry in user.get("email_addresses") or []:
        if entry.get("id") == wanted:
            return entry.get("email_address")
    return None


def account_from_clerk(user: dict[str, Any]) -> Account:
    party = (user.get("public_metadata") or {}).get("party")
    email = primary_email(user)
    who = user.get("username") or email or user["id"]
    if party not in PARTIES:
        raise ClerkLoginError(
            f"{who} is signed in but has no party yet. Ask the operator to run"
            f" `python -m server.auth set-party {email or who} lab|auditor`."
        )
    name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])) or who
    return Account(username=who, party=party, display_name=name, password_hash="")


def set_party(secret_key: str, email: str, party: str, *, transport: Any | None = None) -> str:
    """Set ``public_metadata.party`` on the Clerk user with this email; returns the user id."""
    if party not in PARTIES:
        raise ValueError(f"party must be one of {PARTIES}")
    headers = {"Authorization": f"Bearer {secret_key}"}
    with httpx.Client(timeout=10, transport=transport) as client:
        found = client.get(f"{CLERK_API}/users", params={"email_address": email}, headers=headers)
        found.raise_for_status()
        users = found.json()
        if len(users) != 1:
            raise LookupError(f"{len(users)} Clerk users have the email {email}; expected one")
        user_id = users[0]["id"]
        done = client.patch(
            f"{CLERK_API}/users/{user_id}/metadata",
            json={"public_metadata": {"party": party}},
            headers=headers,
        )
        done.raise_for_status()
    return user_id


# -------------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m server.auth")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="create or update a local account")
    add.add_argument("username")
    add.add_argument("party", choices=PARTIES)
    add.add_argument("--name", default=None, help="display name")
    add.add_argument(
        "--file", type=Path, default=None, help="accounts file (default ui/accounts.json)"
    )
    boot = sub.add_parser(
        "bootstrap", help='first-boot accounts from UI_BOOTSTRAP_ACCOUNTS="user:party:password,..."'
    )
    boot.add_argument("--file", type=Path, default=None)
    who = sub.add_parser(
        "set-party", help="Clerk: bind the user with this email to a party (needs CLERK_SECRET_KEY)"
    )
    who.add_argument("email")
    who.add_argument("party", choices=PARTIES)
    args = parser.parse_args(argv)

    from server.config import load_settings  # local import keeps the CLI free of FastAPI

    settings = load_settings()
    if args.command == "set-party":
        if not settings.clerk_secret_key:
            print("set CLERK_SECRET_KEY (ui/.env or the environment)", file=sys.stderr)
            return 1
        try:
            user_id = set_party(settings.clerk_secret_key, args.email, args.party)
        except (LookupError, httpx.HTTPError) as exc:
            print(f"not set: {exc}", file=sys.stderr)
            return 1
        print(f"{args.email} ({user_id}) is now the {args.party}")
        return 0
    path = args.file or settings.accounts_file
    if args.command == "bootstrap":
        for entry in filter(None, os.environ.get("UI_BOOTSTRAP_ACCOUNTS", "").split(",")):
            username, party, password = entry.strip().split(":", 2)
            if party not in PARTIES or len(password) < 8:
                print(f"skipping {username}: bad party or password shorter than 8", file=sys.stderr)
                continue
            save_account(
                path,
                Account(
                    username=username,
                    party=party,
                    display_name=username,
                    password_hash=hash_password(password),
                ),
            )
            print(f"account {username} ({party}) created in {path}")
        return 0
    password = getpass.getpass(f"password for {args.username}: ")
    if password != getpass.getpass("again: "):
        print("passwords differ", file=sys.stderr)
        return 1
    if len(password) < 8:
        print("use at least 8 characters", file=sys.stderr)
        return 1
    save_account(
        path,
        Account(
            username=args.username,
            party=args.party,
            display_name=args.name or args.username,
            password_hash=hash_password(password),
        ),
    )
    print(f"saved {args.username} ({args.party}) to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
