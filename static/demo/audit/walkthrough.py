"""Interactive live audit walkthrough. Only the logger is stubbed."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2]
REPO = STATIC.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auto", action="store_true", help="Do not pause between steps")
    parser.add_argument("--case", choices=["benign", "bio", "both"], default="both")
    parser.add_argument(
        "--key-directory", type=Path, default=Path.home() / ".local/share/aiab-audit-demo"
    )
    parser.add_argument("--output", type=Path, help="New private artifact directory")
    args = parser.parse_args()
    os.umask(0o077)
    out = args.output or Path(tempfile.mkdtemp(prefix="aiab-live-demo-"))
    if args.output:
        out.mkdir(parents=True, exist_ok=False, mode=0o700)
    out = out.resolve()
    release = STATIC / "deploy/tinfoil/audit/release.json"
    folder = REPO / "plan"
    token = args.key_directory / "lab.token"
    logger = args.key_directory / "logger.key"
    for path in (release, folder / "plan.json", token, logger):
        if not path.exists():
            raise SystemExit(f"Missing prerequisite: {path}")
    cfg = json.loads((folder / "plan.json").read_text())
    pins = json.loads(release.read_text())
    (out / "release.json").write_text(release.read_text())
    shutil.copytree(folder, out / "plan", ignore=shutil.ignore_patterns("__pycache__"))

    def step(title, explanation):
        print(f"\n{'=' * 64}\n{title}\n{explanation}", flush=True)
        if not args.auto and sys.stdin.isatty():
            input("Press Enter to continue… ")

    def cli(*arguments, offline=False):
        base = ["--folder", str(folder), *map(str, arguments)]
        if offline:
            # Enforce no networking while running the same offline CLI verifier.
            command = [
                sys.executable,
                "-c",
                (
                    "import socket,sys; from auditor.baked import main; "
                    "socket.socket=lambda *a,**k: (_ for _ in ()).throw("
                    "RuntimeError('Network disabled for offline verification')); "
                    "main(sys.argv[1:])"
                ),
                *base,
            ]
        else:
            command = [sys.executable, "-m", "auditor.baked", *base]
        result = subprocess.run(command, cwd=STATIC, capture_output=True, text=True, timeout=240)
        if result.returncode:
            # CLI emits fixed errors, not model responses or credentials.
            raise RuntimeError(result.stderr.strip() or "CLI command failed")
        if result.stdout.strip():
            return json.loads(result.stdout)
        return None

    connection = ["--release", release, "--token-file", token]
    step(
        "1. Review the jointly signed plan",
        (
            "This demonstrates the deployed baked plan, not dynamic plan registration.\n"
            "The logger/data are synthetic; the audit service and inference are live.\n"
            "Fixture timestamps intentionally fall within the signed historical window."
        ),
    )
    verified = cli("verify")
    print(
        json.dumps(
            {
                "task": cfg["classification"]["problem"],
                "decision_threshold": cfg["classification"]["threshold"],
                "output_schema": cfg["output_schema"],
                **verified,
            },
            indent=2,
        ),
        flush=True,
    )
    step("2. Verify the live box and pin its report identity", pins["host"])
    cli("pin-identity", *connection, "--output", out / "anchor.json")
    print("Verified attestation, release, runtime image and plan bindings.", flush=True)
    outcomes = []
    cases = ["benign", "bio"] if args.case == "both" else [args.case]
    for case in cases:
        fixture = (
            folder
            / "tests/transcripts"
            / ("benign_coding.jsonl" if case == "benign" else "bio_research.jsonl")
        )
        records = [json.loads(line) for line in fixture.read_text().splitlines()]
        step(
            f"3. Stub logger: {case} example",
            (
                "Print synthetic logs and sign an export using the provisioned demo logger key.\n"
                "This is a real signature over fixture data, not native logger proof."
            ),
        )
        for record in records:
            print(f"[{record['kind']}] {record['content']}", flush=True)
        submission = out / f"{case}-submission.json"
        cli(
            "mock-export",
            "--records",
            fixture,
            "--private",
            logger,
            "--submission-id",
            "walkthrough-" + uuid.uuid4().hex,
            "--output",
            submission,
        )
        step("4. Submit logs to the live audit", "POST /aiab/runs; receive a run ID.")
        admission_path = out / f"{case}-admission.json"
        cli("submit", *connection, "--input", submission, "--output", admission_path)
        admission = json.loads(admission_path.read_text())
        print(json.dumps(admission, indent=2), flush=True)
        bindings = [
            "--run-id",
            admission["run_id"],
            "--input-commitment",
            admission["input_commitment"],
        ]
        step(
            "5. Wait for the live classifier and verify its signed report",
            ("GET /aiab/runs/{run_id}/report. No simulated inference or fallback."),
        )
        report_path = out / f"{case}-report.json"
        cli("report", *connection, *bindings, "--output", report_path)
        envelope = json.loads(report_path.read_text())
        print(json.dumps(envelope, indent=2), flush=True)
        if envelope["report"]["status"] != "completed":
            raise RuntimeError("The service returned a signed failure report")
        step(
            "6. Independently verify the saved report offline",
            (
                "Network calls are disabled in this verifier process. Its trust anchor\n"
                "is the service identity verified online in step 2."
            ),
        )
        verification = cli(
            "verify-offline",
            "--release",
            release,
            "--anchor",
            out / "anchor.json",
            "--report",
            report_path,
            *bindings,
            offline=True,
        )
        (out / f"{case}-offline-verification.json").write_text(json.dumps(verification, indent=2))
        actual = envelope["report"]["result"]["flagged"]
        expected = case == "bio"
        print(
            f"Signature/bindings verified offline. flagged={actual}; expected={expected}",
            flush=True,
        )
        outcomes.append(
            {
                "case": case,
                "run_id": admission["run_id"],
                "flagged": actual,
                "expected": expected,
                "verified": True,
            }
        )
    (out / "summary.json").write_text(json.dumps(outcomes, indent=2))
    print(f"\nArtifacts: {out}\n" + json.dumps(outcomes, indent=2), flush=True)
    if any(item["flagged"] != item["expected"] for item in outcomes):
        raise RuntimeError("Live classification differed from the fixture expectation")
    print("LIVE DEMO PASSED: both signatures and results verified.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"Demo stopped: {exc}. No fallback to mock inference.", file=sys.stderr)
        sys.exit(1)
