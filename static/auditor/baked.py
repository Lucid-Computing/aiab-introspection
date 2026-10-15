"""Sign/verify baked plan folders and submit/fetch independently verified runs."""

import argparse
import asyncio
import base64
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aiab.audit.protocol import AuditError, canonical, decode, digest, sign_bytes
from aiab.baked.bundle import file_digest, load_bundle
from aiab.baked.records import export_statement
from aiab.baked.records import verify as verify_input
from aiab.baked.runtime import verify_report
from aiab.config import read_private_key
from aiab.dryrun import dataset, decision_digest
from auditor.audit import AuditClient, write_new

DEFAULT_TRUST = Path(__file__).resolve().parents[1] / "aiab/baked/trust.json"


def private(path):
    return Ed25519PrivateKey.from_private_bytes(
        base64.b64decode(read_private_key(str(path), "SIGNING_KEY"), validate=True)
    )


def verify_evaluation(folder, bundle, evaluation):
    files, _ = dataset(folder)
    if (
        evaluation.get("schema") != "aiab-folder-evaluation/v1"
        or evaluation.get("mode") != "attested-inference"
        or evaluation.get("decision_digest") != decision_digest(bundle)
        or evaluation.get("test_set_digest") != file_digest(files)
        or evaluation.get("counts", {}).get("errors") != 0
        or not evaluation.get("cases")
    ):
        raise AuditError("incompatible_evaluation")


async def remote(args, bundle):
    release = decode(args.release.read_bytes())
    client = AuditClient(
        release["host"],
        release["repo"],
        release["release_digest"],
        release["policy_digest"],
        read_private_key(str(args.token_file), "AUDIT_TOKEN"),
    )
    try:
        await client.start()
        if (
            client.manifest["plan_digest"] != bundle.digest
            or client.manifest["image_digest"] != release["image_digest"]
        ):
            raise AuditError("release_mismatch")
        if args.command == "pin-identity":
            value = {
                "schema": "aiab-baked-anchor/v1",
                "release": release,
                "manifest": client.manifest,
                "identity": client.identity,
            }
        elif args.command == "submit":
            submission = decode(args.input.read_bytes())
            commitment = verify_input(
                submission, bundle.config, decode(args.trust.read_bytes())["logger_key"]
            )
            status, value = await client.request("POST", "/aiab/runs", submission)
            if status != 202:
                raise AuditError(value.get("error", "submission_failed"))
            value["input_commitment"] = commitment
        else:
            async with asyncio.timeout(bundle.config["limits"]["max_runtime_seconds_per_run"] + 30):
                while True:
                    status, value = await client.request("GET", f"/aiab/runs/{args.run_id}/report")
                    if status == 200:
                        verify_report(
                            value,
                            client.identity,
                            bundle,
                            client.manifest,
                            run_id=args.run_id,
                            input_commitment=args.input_commitment,
                        )
                        break
                    if status != 202:
                        raise AuditError("report_unavailable")
                    await asyncio.sleep(1)
        write_new(args.output, canonical(value) + b"\n")
    finally:
        await client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--trust", type=Path, default=DEFAULT_TRUST)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("digest")
    commands.add_parser("verify")
    sign = commands.add_parser("sign")
    sign.add_argument("--private", type=Path, required=True)
    sign.add_argument("--role", choices=["lab", "auditor"], required=True)
    sign.add_argument("--output", type=Path, required=True)
    commit = commands.add_parser("commit-evaluation")
    commit.add_argument("--evaluation", type=Path, required=True)
    commit.add_argument("--output", type=Path, required=True)
    export = commands.add_parser("mock-export")
    for name in ("records", "private", "output"):
        export.add_argument("--" + name, type=Path, required=True)
    export.add_argument("--submission-id", required=True)
    offline = commands.add_parser("verify-offline")
    for name in ("anchor", "report", "release"):
        offline.add_argument("--" + name, type=Path, required=True)
    for name in ("run-id", "input-commitment"):
        offline.add_argument("--" + name, required=True)
    for name in ("submit", "report", "pin-identity"):
        cmd = commands.add_parser(name)
        for option in ("release", "token-file", "output"):
            cmd.add_argument("--" + option, type=Path, required=True)
        if name == "submit":
            cmd.add_argument("--input", type=Path, required=True)
        if name == "report":
            cmd.add_argument("--run-id", required=True)
            cmd.add_argument("--input-commitment", required=True)
    args = parser.parse_args(argv)
    try:
        trust = decode(args.trust.read_bytes())
        bundle = load_bundle(
            args.folder, trust, signed=args.command not in {"digest", "sign", "commit-evaluation"}
        )
        if args.command in {"digest", "verify"}:
            print(
                canonical(
                    {"plan_digest": bundle.digest, "signatures_verified": args.command == "verify"}
                ).decode()
            )
        elif args.command == "sign":
            key = private(args.private)
            if (
                base64.b64encode(key.public_key().public_bytes_raw()).decode()
                != trust[args.role + "_key"]
            ):
                raise AuditError("wrong_signing_key")
            write_new(
                args.output,
                base64.b64encode(
                    key.sign(sign_bytes("plan-bundle", {"plan_digest": bundle.digest}))
                )
                + b"\n",
                0o644,
            )
        elif args.command == "commit-evaluation":
            evaluation = decode(args.evaluation.read_bytes())
            verify_evaluation(args.folder, bundle, evaluation)
            cfg = bundle.config
            cfg["classification"]["evaluation"] = {
                "test_set_digest": evaluation["test_set_digest"],
                "results_digest": digest(evaluation),
            }
            write_new(args.output, canonical(cfg) + b"\n")
        elif args.command == "mock-export":
            key = private(args.private)
            if (
                base64.b64encode(key.public_key().public_bytes_raw()).decode()
                != trust["logger_key"]
            ):
                raise AuditError("wrong_logger_key")
            records = [
                decode(line) for line in args.records.read_bytes().splitlines() if line.strip()
            ]
            scope = bundle.config["input_scope"]
            statement = export_statement(records, scope)
            evidence = {
                "statement": statement,
                "signature": base64.b64encode(
                    key.sign(sign_bytes("record-export", statement))
                ).decode(),
            }
            write_new(
                args.output,
                canonical(
                    {
                        "submission_id": args.submission_id,
                        "plan_digest": bundle.digest,
                        "input_scope": scope,
                        "records": records,
                        "logger_evidence": evidence,
                    }
                )
                + b"\n",
            )
        elif args.command == "verify-offline":
            anchor, release = decode(args.anchor.read_bytes()), decode(args.release.read_bytes())
            if (
                anchor["release"] != release
                or digest(anchor["manifest"]) != release["policy_digest"]
                or anchor["manifest"]["image_digest"] != release["image_digest"]
            ):
                raise AuditError("anchor_mismatch")
            report = verify_report(
                decode(args.report.read_bytes()),
                anchor["identity"],
                bundle,
                anchor["manifest"],
                run_id=args.run_id,
                input_commitment=args.input_commitment,
            )
            print(
                canonical(
                    {
                        "verified": True,
                        "trust_basis": "independently-pinned-live-identity",
                        "report": report,
                    }
                ).decode()
            )
        else:
            asyncio.run(remote(args, bundle))
    except Exception as exc:
        raise SystemExit(
            exc.code if isinstance(exc, AuditError) else "invalid_input_or_configuration"
        ) from None


if __name__ == "__main__":
    main()
