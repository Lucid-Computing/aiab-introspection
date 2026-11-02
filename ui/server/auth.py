"""Who is logged in, and which party they are: OpenID Connect login (Okta), the signed
session cookie, and the local accounts the UI used before.

The identity provider authenticates; the UI's own cookie authorises. ``GET /login/start``
sends the browser to the provider's authorization endpoint (authorization code flow with
PKCE, state and nonce kept in a short-lived signed cookie). ``GET /login/callback``
exchanges the code, checks the ID token against the provider's key set, and the app
issues the same ``SessionSigner`` cookie as before, carrying the party. The party comes
from the provider's groups claim: a member of the lab group is the lab, of the auditor
group the auditor. In neither, or in both, cannot log in.

Any OpenID Connect provider works; Okta is the one this was written against. Without the
provider settings the previous system stays: local accounts with hashed passwords in
``accounts.json`` (``python -m server.auth add USERNAME PARTY``). That path is for laptops
and for rollback; see docs/OIDC_AUTH_MIGRATION.md.
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
from urllib.parse import urlencode

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


# ------------------------------------------------------------------------- OpenID Connect

LOGIN_STATE_TTL_SECONDS = 10 * 60  # the browser has this long to come back from the provider
JWKS_MIN_REFETCH_SECONDS = 60  # an unknown key id refetches the key set, at most this often


class LoginError(Exception):
    """The provider's answer is not acceptable; the message is shown to the user."""


class Stamp:
    """A short-lived signed dict in a cookie: the state, nonce and PKCE verifier of a login
    in flight. Same HMAC scheme as the session cookie, separate purpose string."""

    def __init__(self, secret: bytes, ttl: int = LOGIN_STATE_TTL_SECONDS) -> None:
        self._secret, self._ttl = secret, ttl

    def _mac(self, payload: str) -> str:
        tagged = b"login:" + payload.encode("ascii")
        return _b64(hmac.new(self._secret, tagged, hashlib.sha256).digest())

    def seal(self, data: dict[str, str]) -> str:
        body = dict(data, exp=int(time.time()) + self._ttl)
        payload = _b64(json.dumps(body, separators=(",", ":")).encode("utf-8"))
        return f"{payload}.{self._mac(payload)}"

    def open(self, token: str | None) -> dict[str, Any] | None:
        if not token or "." not in token:
            return None
        payload, mac = token.rsplit(".", 1)
        if not hmac.compare_digest(mac, self._mac(payload)):
            return None
        try:
            body = json.loads(_unb64(payload))
        except ValueError:
            return None
        return body if body.get("exp", 0) >= time.time() else None


def pkce_pair() -> tuple[str, str]:
    verifier = _b64(secrets.token_bytes(48))
    challenge = _b64(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


class OidcLogin:
    """The authorization code flow against one OpenID Connect provider, and the mapping
    from its groups claim to a party. ``transport`` is for tests."""

    def __init__(
        self,
        issuer: str,
        client_id: str,
        client_secret: str,
        *,
        group_lab: str,
        group_auditor: str,
        groups_claim: str = "groups",
        scopes: str = "openid profile email groups",
        transport: Any | None = None,
        leeway: int = 10,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self._secret = client_secret
        self.groups = {group_lab: "lab", group_auditor: "auditor"}
        self.groups_claim = groups_claim
        self.scopes = scopes
        self._leeway = leeway
        self._transport = transport
        self._lock = threading.RLock()  # _key_for holds it while metadata() takes it
        self._metadata: dict[str, Any] | None = None
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=10, transport=self._transport)

    # -- the provider's metadata and keys

    def metadata(self) -> dict[str, Any]:
        """The provider's discovery document, fetched once."""
        with self._lock:
            if self._metadata is None:
                try:
                    with self._client() as client:
                        response = client.get(f"{self.issuer}/.well-known/openid-configuration")
                        response.raise_for_status()
                        data = response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    raise LoginError("The identity provider did not answer; try again.") from exc
                if data.get("issuer") != self.issuer:
                    raise LoginError(
                        f"The provider at {self.issuer} calls itself {data.get('issuer')}."
                    )
                self._metadata = data
            return self._metadata

    def _load_keys(self) -> None:
        with self._client() as client:
            response = client.get(self.metadata()["jwks_uri"])
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
            raise LoginError("The ID token is not readable.") from exc
        with self._lock:
            if kid not in self._keys and time.time() - self._fetched_at > JWKS_MIN_REFETCH_SECONDS:
                try:
                    self._load_keys()
                except (httpx.HTTPError, ValueError) as exc:
                    raise LoginError("The identity provider did not answer; try again.") from exc
            key = self._keys.get(kid)
        if key is None:
            raise LoginError("The ID token was signed with a key this UI does not know.")
        return key

    # -- the flow

    def authorize_url(self, redirect_uri: str, state: str, nonce: str, challenge: str) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "scope": self.scopes,
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self.metadata()['authorization_endpoint']}?{query}"

    def exchange(self, code: str, redirect_uri: str, verifier: str) -> dict[str, Any]:
        """The token endpoint's answer for an authorization code (client_secret_basic)."""
        try:
            with self._client() as client:
                response = client.post(
                    self.metadata()["token_endpoint"],
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": redirect_uri,
                        "code_verifier": verifier,
                    },
                    auth=(self.client_id, self._secret),
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise LoginError("The identity provider did not answer; try again.") from exc
        if response.status_code != 200:
            detail = ""
            try:
                detail = response.json().get("error_description") or response.json().get("error")
            except ValueError:
                pass
            raise LoginError(f"The identity provider refused the login code. {detail}".strip())
        return response.json()

    def claims(self, id_token: str, nonce: str) -> dict[str, Any]:
        """The verified ID token: signature, issuer, audience, expiry and nonce."""
        key = self._key_for(id_token)
        try:
            claims = jwt.decode(
                id_token,
                key,
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=self.issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise LoginError(f"The ID token did not verify ({exc}).") from exc
        if claims.get("nonce") != nonce:
            raise LoginError("The ID token does not belong to this login attempt.")
        return claims

    def account(self, claims: dict[str, Any]) -> Account:
        """The ID token's holder as an Account: the party from the groups claim, the
        username from preferred_username (its local part) or the email, the display name."""
        groups = claims.get(self.groups_claim) or []
        if isinstance(groups, str):
            groups = [groups]
        parties = {self.groups[g] for g in groups if g in self.groups}
        login = claims.get("preferred_username") or claims.get("email") or claims["sub"]
        if len(parties) != 1:
            wanted = " or ".join(self.groups)
            if parties:
                raise LoginError(f"{login} is in both party groups ({wanted}); it must be in one.")
            raise LoginError(
                f"{login} is signed in but is in neither party group. Ask the Okta"
                f" administrator to add you to {wanted}."
            )
        username = login.split("@", 1)[0] if "@" in login else login
        name = claims.get("name") or " ".join(
            filter(None, [claims.get("given_name"), claims.get("family_name")])
        )
        return Account(
            username=username, party=parties.pop(), display_name=name or username, password_hash=""
        )

    def login(self, code: str, redirect_uri: str, verifier: str, nonce: str) -> tuple[Account, str]:
        """Code to Account, plus the raw ID token (for the provider's logout)."""
        tokens = self.exchange(code, redirect_uri, verifier)
        id_token = tokens.get("id_token")
        if not id_token:
            raise LoginError("The identity provider returned no ID token.")
        return self.account(self.claims(id_token, nonce)), id_token

    def logout_url(self, id_token: str, post_logout_redirect_uri: str) -> str | None:
        """The provider's end-session address, so Log out ends the Okta session too and the
        next Sign in asks who you are. None if the provider has none."""
        try:
            endpoint = self.metadata().get("end_session_endpoint")
        except LoginError:
            return None
        if not endpoint:
            return None
        query = urlencode(
            {"id_token_hint": id_token, "post_logout_redirect_uri": post_logout_redirect_uri}
        )
        return f"{endpoint}?{query}"


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
    args = parser.parse_args(argv)

    from server.config import load_settings  # local import keeps the CLI free of FastAPI

    path = args.file or load_settings().accounts_file
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
