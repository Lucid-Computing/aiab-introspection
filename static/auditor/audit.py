"""Plan signing and attested audit client. Private keys never enter the box."""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aiab.audit.protocol import (
    AuditError,
    canonical,
    decode,
    digest,
    sign_bytes,
    verify_signature,
)
from aiab.audit.report import verify_report
from aiab.audit.transport import PinnedConnection
from aiab.config import read_private_key


class AuditClient:
    def __init__(self, host: str, repo: str, release_digest: str, policy_digest: str, token: str):
        self.connection = PinnedConnection(host, repo, release_digest)
        self.policy_digest = policy_digest
        self.token = token
        self.identity = None

    async def start(self):
        await self.connection.start()
        status, manifest = await self.connection.request("GET", "/aiab/manifest")
        if status != 200 or digest(manifest) != self.policy_digest:
            raise AuditError("policy_mismatch")
        status, identity = await self.connection.request("GET", "/aiab/pubkey")
        if status != 200 or identity.get("policy_digest") != self.policy_digest:
            raise AuditError("identity_mismatch")
        self.identity = identity

    async def request(self, method: str, path: str, value: dict | None = None):
        if self.identity is None:
            raise AuditError("client_not_verified")
        status, result = await self.connection.request(
            method, path, canonical(value) if value is not None else None, self.token
        )
        # A restart cannot silently splice one boot's admissions and another's results.
        identity_status, current = await self.connection.request("GET", "/aiab/pubkey")
        if identity_status != 200 or current != self.identity:
            raise AuditError("identity_changed")
        return status, result

    async def poll(self, run_id: str, plan: dict, commitment: str, timeout: float = 330):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status, result = await self.request("GET", f"/aiab/runs/{run_id}/report")
            if status == 200:
                verify_report(
                    result,
                    self.identity,
                    plan,
                    run_id=run_id,
                    input_commitment=commitment,
                    policy_digest=self.policy_digest,
                )
                return result
            if status != 202:
                raise AuditError("report_unavailable")
            await asyncio.sleep(1)
        raise AuditError("report_timeout")

    async def close(self):
        await self.connection.close()


def write_new(path: Path, data: bytes, mode: int = 0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as file:
        file.write(data)


def sign_plan(plan: dict, private: Ed25519PrivateKey, role: str) -> str:
    key = base64.b64encode(private.public_key().public_bytes_raw()).decode()
    if plan["parties"][role + "_key"] != key:
        raise AuditError("wrong_signing_key")
    return base64.b64encode(private.sign(sign_bytes("plan", plan))).decode()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    keygen = commands.add_parser("keygen")
    keygen.add_argument("--private", type=Path, required=True)
    keygen.add_argument("--public", type=Path, required=True)
    sign = commands.add_parser("sign")
    sign.add_argument("--plan", type=Path, required=True)
    sign.add_argument("--private", required=True)
    sign.add_argument("--role", choices=["lab", "auditor"], required=True)
    sign.add_argument("--output", type=Path, required=True)
    assemble = commands.add_parser("assemble")
    assemble.add_argument("--plan", type=Path, required=True)
    assemble.add_argument("--lab-signature", type=Path, required=True)
    assemble.add_argument("--auditor-signature", type=Path, required=True)
    assemble.add_argument("--output", type=Path, required=True)
    digest_parser = commands.add_parser("digest")
    digest_parser.add_argument("file", type=Path)
    for name in ("register", "submit", "report"):
        cmd = commands.add_parser(name)
        for option in ("host", "repo", "release-digest", "policy-digest", "token-file"):
            cmd.add_argument("--" + option, required=True)
        cmd.add_argument("--output", type=Path, required=True)
        if name != "report":
            cmd.add_argument("--input", type=Path, required=True)
        else:
            cmd.add_argument("--run-id", required=True)
            cmd.add_argument("--plan", type=Path, required=True)
            cmd.add_argument("--input-commitment", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "keygen":
            key = Ed25519PrivateKey.generate()
            private = key.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
            write_new(args.private, base64.b64encode(private) + b"\n")
            write_new(
                args.public, base64.b64encode(key.public_key().public_bytes_raw()) + b"\n", 0o644
            )
        elif args.command == "sign":
            plan = decode(args.plan.read_bytes())
            key = Ed25519PrivateKey.from_private_bytes(
                base64.b64decode(read_private_key(args.private, "PLAN_PRIVATE_KEY"), validate=True)
            )
            write_new(args.output, sign_plan(plan, key, args.role).encode() + b"\n", 0o644)
        elif args.command == "assemble":
            plan = decode(args.plan.read_bytes())
            signatures = {
                role: getattr(args, role + "_signature").read_text().strip()
                for role in ("lab", "auditor")
            }
            for role, signature in signatures.items():
                verify_signature(plan["parties"][role + "_key"], signature, "plan", plan)
            write_new(
                args.output, canonical({"plan": plan, "signatures": signatures}) + b"\n", 0o644
            )
        elif args.command == "digest":
            print(digest(decode(args.file.read_bytes())))
        else:
            asyncio.run(remote(args))
    except Exception as exc:
        code = exc.code if isinstance(exc, AuditError) else "invalid_input_or_configuration"
        raise SystemExit(code) from None


async def remote(args):
    token = read_private_key(args.token_file, "AUDIT_TOKEN")
    client = AuditClient(args.host, args.repo, args.release_digest, args.policy_digest, token)
    try:
        await client.start()
        if args.command == "report":
            document = decode(args.plan.read_bytes())
            plan = document.get("plan", document)
            result = await client.poll(args.run_id, plan, args.input_commitment)
        else:
            path = "/aiab/plans" if args.command == "register" else "/aiab/runs"
            status, result = await client.request("POST", path, decode(args.input.read_bytes()))
            if status not in (200, 201, 202):
                raise AuditError("request_rejected")
        write_new(args.output, canonical(result) + b"\n", 0o600)
    finally:
        await client.close()


if __name__ == "__main__":
    main()
