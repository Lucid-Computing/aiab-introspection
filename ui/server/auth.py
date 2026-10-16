"""Local accounts (username, party, hashed password) and signed session cookies.

GitHub login replaces this once the UI writes to pull requests; the party model stays.
``python -m server.auth add USERNAME PARTY`` creates or updates an account.
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
import time
from dataclasses import asdict, dataclass
from pathlib import Path

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
