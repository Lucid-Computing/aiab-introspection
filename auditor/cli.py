"""Auditor verify, report, and rollup commands with an optional continuity checkpoint."""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from aiab.reporter import COUNTERS, canonical_json
from auditor.attestation import AttestedClient, production_inputs, verify_attestation
from auditor.checkpoint import checkpoint_warnings, load_checkpoint, save_checkpoint
from auditor.verify import VerificationError, VerifiedReports, strict_json, verify_reports

LIMITATION = (
    "Request share is not token/compute share or verified research prevalence. "
    "Small samples and classification failures limit interpretation; these are descriptive "
    "sample results, not population estimates."
)
CONTINUITY_LIMITATION = (
    "Continuity warnings mean incomplete evidence, not proof of malicious behavior. "
    "This checkpoint cannot detect forks or restarts never observed by this auditor."
)


def local_origin(host: str) -> str:
    """Parse one exact loopback HTTPS authority before allowing insecure transport."""
    if not host or re.search(r"[\s\\?#%]", host):
        raise VerificationError("Insecure local host must be localhost or 127.0.0.1")
    try:
        parsed = urlsplit(host if "://" in host else f"https://{host}")
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"localhost", "127.0.0.1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.netloc.endswith(":")
        ):
            raise ValueError
        port = parsed.port if parsed.port is not None else 8443
        if not 1 <= port <= 65535:
            raise ValueError
        return f"https://{parsed.hostname}:{port}"
    except ValueError:
        raise VerificationError(
            "--insecure-local requires HTTPS with host localhost or 127.0.0.1 and optional port"
        ) from None


class ArtifactClient(Protocol):
    def get(self, path: str) -> httpx.Response: ...


def fetch_artifacts(client: ArtifactClient, *, reports: bool) -> tuple[Any, Any, Any]:
    def get(path: str) -> Any:
        response = client.get(path)
        if response.status_code != 200:
            raise VerificationError("Audit endpoint returned an unsuccessful status")
        return strict_json(response.content)

    identity = get("/aiab/pubkey")
    manifest = get("/aiab/manifest")
    envelopes = get("/aiab/reports") if reports else []
    # Catch a reboot even when history is empty or a restart follows the report fetch.
    if get("/aiab/pubkey") != identity:
        raise VerificationError("AIAB changed boot identity while fetching; fetch again")
    return identity, manifest, envelopes


def _counts(report: dict[str, Any]) -> str:
    classified = report["requests_classified"]
    flagged = report["requests_flagged"]
    percentage = f"{100 * flagged / classified:.1f}%" if classified else "N/A"
    return (
        f"{report['requests_selected']} selected of {report['requests_total']} admitted at this "
        f"proxy; {classified} successfully classified, {report['classifier_errors']} "
        f"classification failures, {flagged} flagged ({percentage} of successful classifications), "
        f"{report['requests_blocked']} blocked; policy {report['policy_digest']}"
    )


def format_rollup(verified: VerifiedReports) -> str:
    lines = [
        f"{report['period_start']} – {report['period_end']} [{report['enforcement_mode']}]: "
        + _counts(report)
        for report in verified.reports
    ]
    totals = {key: sum(report[key] for report in verified.reports) for key in COUNTERS}
    totals["policy_digest"] = verified.identity["policy_digest"]
    lines.append(
        f"Totals ({len(verified.reports)} unique periods; boot {verified.identity['boot_id']}): "
        + _counts(totals)
    )
    lines.append(LIMITATION)
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify AIAB artifacts and inspect signed reports")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("verify", "report", "rollup"):
        sub = commands.add_parser(command)
        sub.add_argument("--host", required=True)
        sub.add_argument("--repo", help="Reviewed Tinfoil config repository (owner/repo)")
        sub.add_argument(
            "--expected-digest",
            default=os.getenv("EXPECTED_AIAB_DIGEST"),
            help="Approved Tinfoil release tinfoil.hash digest, not Docker image digest",
        )
        sub.add_argument("--expected-policy-digest", default=os.getenv("EXPECTED_POLICY_DIGEST"))
        sub.add_argument("--insecure-local", action="store_true")
        if command != "verify":
            sub.add_argument("--state-file", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    try:
        evidence = None
        if args.insecure_local:
            origin = local_origin(args.host)
            print(
                "WARNING: INSECURE LOCAL MODE — TLS identity and enclave attestation are NOT "
                "verified. A local endpoint can replace both reports and signing keys.",
                file=sys.stderr,
            )
        else:
            production_inputs(
                args.host, args.repo, args.expected_digest, args.expected_policy_digest
            )
            evidence = verify_attestation(args.host, args.repo, args.expected_digest)
        state_file = getattr(args, "state_file", None)
        previous = load_checkpoint(state_file) if state_file else None
        if evidence is not None:
            with AttestedClient(evidence) as client:
                identity, manifest, envelopes = fetch_artifacts(
                    client, reports=args.command != "verify"
                )
        else:
            # Environment proxies and redirects could escape the loopback boundary.
            with httpx.Client(
                base_url=origin, verify=False, trust_env=False, follow_redirects=False, timeout=15
            ) as client:
                identity, manifest, envelopes = fetch_artifacts(
                    client, reports=args.command != "verify"
                )
        verified = verify_reports(
            identity, manifest, envelopes, expected_policy_digest=args.expected_policy_digest
        )
        warnings = list(verified.warnings)
        if args.command != "verify":
            warnings.extend(checkpoint_warnings(verified, previous, now=datetime.now(UTC)))
        if state_file and previous is None:
            warnings.append("First checkpoint use: earlier continuity is unknown")
        for warning in warnings:
            print(f"WARNING: {warning}.", file=sys.stderr)
        if warnings or state_file:
            print(CONTINUITY_LIMITATION, file=sys.stderr)
        if state_file:
            save_checkpoint(state_file, verified, previous)
        if evidence is None:
            print("Image digest: UNVERIFIED (insecure local mode)")
        else:
            print(f"Tinfoil release digest: {evidence.release_digest}")
        print(f"Policy digest: {verified.identity['policy_digest']}")
        if args.expected_digest and args.insecure_local:
            print(
                "WARNING: Expected image digest cannot be checked in local mode.", file=sys.stderr
            )
        if args.command == "verify":
            if evidence is None:
                print("Local manifest and public identity are consistent; no attestation claim.")
            else:
                print("Attested release, approved policy and public identity verified.")
        elif args.command == "report":
            for report in verified.reports:
                print(canonical_json(report))
        else:
            print(format_rollup(verified))
    except VerificationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    except (httpx.HTTPError, OSError, UnicodeError):
        print("ERROR: Audit transport or checkpoint I/O failed.", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
