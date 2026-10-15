"""Evaluate any supported jointly signed plan through the real attested service.

Input is a private JSON list of {id, text, expected}. Labels are complete result
values. No raw input text is written into evaluation output. The logger export is
synthetic and genuinely signed; this is not native logger completeness evidence.
"""

import argparse
import asyncio
import base64
import re
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aiab.audit.logger import SignedTextVerifier, export_statement
from aiab.audit.plan import Trust, accept_plan
from aiab.audit.protocol import AuditError, canonical, clone, decode, digest, sign_bytes
from aiab.audit.report import check_result
from aiab.config import read_private_key
from auditor.audit import AuditClient, write_new


def decision_digest(plan):
    decision = clone(plan)
    decision.pop("plan_id")
    decision["classification"].pop("evaluation")
    return digest(decision)


def validate_dataset(dataset, plan, positive, negative):
    if not isinstance(dataset, list) or not 1 <= len(dataset) <= 100:
        raise AuditError("invalid_dataset")
    if canonical(positive) == canonical(negative):
        raise AuditError("identical_labels")
    check_result(positive, plan)
    check_result(negative, plan)
    seen = set()
    for item in dataset:
        if not isinstance(item, dict) or set(item) != {"id", "text", "expected"}:
            raise AuditError("invalid_dataset")
        if not isinstance(item["id"], str) or not 1 <= len(item["id"]) <= 64 or item["id"] in seen:
            raise AuditError("invalid_dataset")
        seen.add(item["id"])
        if not isinstance(item["text"], str) or not item["text"]:
            raise AuditError("invalid_dataset")
        if len(item["text"].encode()) > plan["limits"]["max_input_utf8_bytes"]:
            raise AuditError("input_too_large")
        if canonical(item["expected"]) not in (canonical(positive), canonical(negative)):
            raise AuditError("invalid_label")


def summarize(cases):
    counts = {k: 0 for k in ("tp", "tn", "fp", "fn", "errors")}
    for case in cases:
        counts[case["outcome"]] += 1
    return {
        "counts": counts,
        "fpr": {"numerator": counts["fp"], "denominator": counts["fp"] + counts["tn"]},
        "fnr": {"numerator": counts["fn"], "denominator": counts["fn"] + counts["tp"]},
        "error_rate": {"numerator": counts["errors"], "denominator": len(cases)},
        "note": "FPR/FNR exclude errors; zero denominator means undefined",
    }


async def evaluate(args):
    signed = decode(args.plan.read_bytes())
    plan = signed["plan"]
    dataset = decode(args.dataset.read_bytes())
    positive, negative = decode(args.positive), decode(args.negative)
    validate_dataset(dataset, plan, positive, negative)
    key = Ed25519PrivateKey.from_private_bytes(
        base64.b64decode(
            read_private_key(str(args.logger_key), "EVALUATION_LOGGER_KEY"), validate=True
        )
    )
    if (
        base64.b64encode(key.public_key().public_bytes_raw()).decode()
        != plan["input_scope"]["logger_key"]
    ):
        raise AuditError("wrong_logger_key")
    token = read_private_key(str(args.token_file), "EVALUATION_TOKEN")
    client = AuditClient(args.host, args.repo, args.release_digest, args.policy_digest, token)
    cases = []
    artifact = {
        "schema": "aiab-evaluation/v1",
        "dataset_digest": digest(dataset),
        "decision_digest": decision_digest(plan),
        "plan_digest": digest(plan),
        "release_digest": args.release_digest,
        "policy_digest": args.policy_digest,
        "positive": positive,
        "negative": negative,
        "cases": cases,
        "logger": "synthetic signed exporter",
        "status": "incomplete",
    }
    try:
        await client.start()
        manifest = client.manifest
        trust = Trust(
            **{
                k: manifest[k]
                for k in (
                    "runtime_digest",
                    "lab_key",
                    "auditor_key",
                    "logger_key",
                    "model",
                    "max_plans",
                )
            }
        )
        accept_plan(signed, trust)
        status, _ = await client.request("POST", "/aiab/plans", signed)
        if status not in (200, 201):
            raise AuditError("plan_rejected")
        artifact["identity"] = client.identity
        for index, item in enumerate(dataset):
            chunks = item["text"].encode().splitlines(keepends=True)
            statement = export_statement(
                chunks, plan["input_scope"], previous_head="sha256:" + "0" * 64, first_sequence=0
            )
            evidence = {
                "statement": statement,
                "signature": base64.b64encode(
                    key.sign(sign_bytes("logger-export", statement))
                ).decode(),
                "chunks": [base64.b64encode(c).decode() for c in chunks],
            }
            commitment = SignedTextVerifier().verify(item["text"], evidence, plan["input_scope"])
            started = time.monotonic()
            case = {
                "case_digest": digest(item),
                "input_commitment": commitment,
                "expected": item["expected"],
                "outcome": "errors",
            }
            cases.append(case)
            try:
                status, admission = await client.request(
                    "POST",
                    "/aiab/runs",
                    {
                        "submission_id": args.run_prefix + "-" + str(index),
                        "plan_digest": digest(plan),
                        "text": item["text"],
                        "logger_evidence": evidence,
                    },
                )
                if status != 202:
                    raise AuditError("admission_rejected")
                result = await client.poll(admission["run_id"], plan, commitment)
                case["envelope"] = result
                report = result["report"]
                if report["status"] == "completed":
                    actual = canonical(report["result"])
                    if actual in (canonical(positive), canonical(negative)):
                        expected_positive = canonical(item["expected"]) == canonical(positive)
                        actual_positive = actual == canonical(positive)
                        case["outcome"] = ("t" if expected_positive == actual_positive else "f") + (
                            "p" if actual_positive else "n"
                        )
                    else:
                        case["error"] = "unclassified_result"
                else:
                    case["error"] = report["error"]
            except AuditError as exc:
                case["error"] = exc.code
            case["elapsed_seconds"] = round(time.monotonic() - started, 3)
        artifact["status"] = "completed"
    finally:
        artifact.update(summarize(cases))
        write_new(args.output, canonical(artifact) + b"\n")
        await client.close()
    print(canonical({"status": artifact["status"], **summarize(cases)}).decode())
    return 1 if artifact["counts"]["errors"] else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("host", "repo", "release-digest", "policy-digest", "run-prefix"):
        parser.add_argument("--" + option, required=True)
    for option in ("plan", "dataset", "logger-key", "token-file", "output"):
        parser.add_argument("--" + option, type=Path, required=True)
    parser.add_argument("--positive", default='{"flagged":true}')
    parser.add_argument("--negative", default='{"flagged":false}')
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,110}", args.run_prefix):
        parser.error("Run prefix must be 1-110 letters, digits, underscores or hyphens")
    if args.output.exists():
        parser.error("Output already exists")
    try:
        raise SystemExit(asyncio.run(evaluate(args)))
    except AuditError as exc:
        raise SystemExit(exc.code) from None


if __name__ == "__main__":
    main()
