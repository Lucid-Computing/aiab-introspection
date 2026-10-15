"""Synthetic demo logger. Real signatures/chains; no claim of WORM integration."""

import argparse
import base64
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aiab.audit.logger import export_statement
from aiab.audit.protocol import canonical, decode, digest, sign_bytes
from aiab.config import read_private_key
from auditor.audit import write_new


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--text", type=Path, required=True)
    parser.add_argument("--private", required=True)
    parser.add_argument("--submission-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = decode(args.plan.read_bytes())
    plan = document.get("plan", document)
    text = args.text.read_bytes()
    key = Ed25519PrivateKey.from_private_bytes(
        base64.b64decode(read_private_key(args.private, "MOCK_LOGGER_KEY"), validate=True)
    )
    if (
        base64.b64encode(key.public_key().public_bytes_raw()).decode()
        != plan["input_scope"]["logger_key"]
    ):
        raise SystemExit("Logger key differs from signed plan")
    chunks = text.splitlines(keepends=True)
    statement = export_statement(
        chunks, plan["input_scope"], previous_head="sha256:" + "0" * 64, first_sequence=0
    )
    evidence = {
        "statement": statement,
        "signature": base64.b64encode(key.sign(sign_bytes("logger-export", statement))).decode(),
        "chunks": [base64.b64encode(chunk).decode() for chunk in chunks],
    }
    submission = {
        "submission_id": args.submission_id,
        "plan_digest": digest(plan),
        "text": text.decode("utf-8"),
        "logger_evidence": evidence,
    }
    write_new(args.output, canonical(submission) + b"\n")


if __name__ == "__main__":
    main()
