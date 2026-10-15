"""Real deployed folder-plan acceptance; only committed synthetic record exports."""

import asyncio
import base64
from pathlib import Path

from aiab.audit.protocol import canonical, clone, decode, digest, sign_bytes
from aiab.baked.bundle import file_digest, load_bundle
from aiab.baked.records import export_statement, verify
from aiab.baked.runtime import verify_report
from aiab.config import read_private_key
from aiab.dryrun import dataset, decision_digest
from auditor.audit import AuditClient, write_new
from auditor.baked import private
from auditor.evaluate import summarize


async def run(args):
    folder = Path(__file__).resolve().parents[3] / "plan"
    trust = decode(args.trust.read_bytes())
    bundle = load_bundle(folder, trust)
    client = AuditClient(
        args.host,
        args.repo,
        args.release_digest,
        args.policy_digest,
        read_private_key(str(args.key_directory / "lab.token"), "AUDIT_TOKEN"),
    )
    logger = private(args.key_directory / "logger.key")
    artifact = {
        "schema": "aiab-baked-acceptance/v1",
        "status": "incomplete",
        "checks": {},
        "reports": [],
        "plan_digest": bundle.digest,
    }
    try:
        await client.start()
        artifact.update(
            identity=client.identity, manifest=client.manifest, release_digest=args.release_digest
        )
        assert client.manifest["plan_digest"] == bundle.digest
        assert client.manifest["image_digest"] == trust["runtime_digest"]
        artifact["checks"]["baked_plan_and_image"] = True
        status, _ = await client.request("POST", "/aiab/plans", {})
        assert status in (404, 405)
        artifact["checks"]["runtime_registration_disabled"] = True
        files, labels = dataset(folder)
        cases = []
        for index, (name, raw) in enumerate(sorted(files.items())):
            if not name.endswith(".jsonl"):
                continue
            records = [decode(line) for line in raw.splitlines() if line.strip()]
            scope = bundle.config["input_scope"]
            statement = export_statement(records, scope)
            submission = {
                "submission_id": args.run_prefix + str(index),
                "plan_digest": bundle.digest,
                "input_scope": scope,
                "records": records,
                "logger_evidence": {
                    "statement": statement,
                    "signature": base64.b64encode(
                        logger.sign(sign_bytes("record-export", statement))
                    ).decode(),
                },
            }
            commitment = verify(submission, bundle.config, trust["logger_key"])
            if not cases:
                for attack in ("plan", "text", "signature"):
                    bad = clone(submission)
                    if attack == "plan":
                        bad["plan_digest"] = "sha256:" + "0" * 64
                    if attack == "text":
                        bad["records"][0]["content"] += "tampered"
                    if attack == "signature":
                        bad["logger_evidence"]["signature"] = "A" * 88
                    status, _ = await client.request("POST", "/aiab/runs", bad)
                    assert status == 400
                    artifact["checks"]["reject_" + attack] = True
                status, _ = await client.connection.request(
                    "POST", "/aiab/runs", canonical(submission), "incorrect-token"
                )
                assert status == 401
                artifact["checks"]["unauthorized_rejected"] = True
            status, admission = await client.request("POST", "/aiab/runs", submission)
            assert status == 202
            status, retry = await client.request("POST", "/aiab/runs", submission)
            assert status == 202 and retry["run_id"] == admission["run_id"]
            async with asyncio.timeout(bundle.config["limits"]["max_runtime_seconds_per_run"] + 30):
                while True:
                    status, envelope = await client.request(
                        "GET", f"/aiab/runs/{admission['run_id']}/report"
                    )
                    if status == 200:
                        break
                    assert status == 202
                    await asyncio.sleep(1)
            report = verify_report(
                envelope,
                client.identity,
                bundle,
                client.manifest,
                run_id=admission["run_id"],
                input_commitment=commitment,
            )
            artifact["reports"].append({"name": Path(name).name, "envelope": envelope})
            assert report["status"] == "completed"
            label = labels[Path(name).name]["flagged"]
            result = report["result"]
            actual = result["flagged"]
            cases.append(
                {
                    "case_digest": digest({"name": name, "records": records}),
                    "expected": {"flagged": label},
                    "result": result,
                    "outcome": ("t" if actual == label else "f") + ("p" if actual else "n"),
                }
            )
        evaluation = {
            "schema": "aiab-folder-evaluation/v1",
            "mode": "attested-inference",
            "decision_digest": decision_digest(bundle),
            "test_set_digest": file_digest(files),
            "cases": cases,
            **summarize(cases),
        }
        artifact["evaluation"] = evaluation
        artifact["checks"]["idempotency"] = True
        artifact["checks"]["signed_schema_valid_results"] = True
        artifact["checks"]["expected_classifications"] = all(
            c["outcome"] in ("tp", "tn") for c in cases
        )
        assert artifact["checks"]["expected_classifications"]
        artifact["status"] = "passed"
    finally:
        write_new(args.output, canonical(artifact) + b"\n")
        await client.close()
    print(artifact["status"])
