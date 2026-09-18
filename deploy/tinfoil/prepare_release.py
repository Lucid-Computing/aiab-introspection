"""Create a minimal, local-only public config candidate. Never publish anything."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from aiab.reporter import canonical_json, policy_digest
from auditor.policy import expected_manifest

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = {
    "AIAB_MODE": "tinfoil",
    "AIAB_ID": "aiab-introspection",
    "ENFORCEMENT_MODE": "block",
    "BLOCK_THRESHOLD": "0.8",
    "INFERENCE_PROVIDER": "anthropic",
    "INFERENCE_URL": "https://api.anthropic.com",
    "ANTHROPIC_MODEL": "claude-sonnet-4-6",
    "AIAB_REQUIRE_GATEWAY_TOKEN": "true",
    "LISTEN_PORT": "8080",
    "SAMPLE_RATE": "0.05",
    "REPORT_PERIOD_SECONDS": "10",
    "REPORT_HISTORY": "100",
    "CLASSIFIER_MODEL": "gpt-oss-safeguard-120b",
    "CLASSIFIER_DEFINITION": "classifiers/ai_capabilities.md",
    "LOG_LEVEL": "INFO",
}


def prepare(output: Path, image: str, digest: str | None) -> str:
    if not re.fullmatch(r"ghcr\.io/[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]*", image):
        raise ValueError("Expected a lowercase ghcr.io/owner/image name")
    if digest is not None and (
        not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) or digest == "sha256:" + "0" * 64
    ):
        raise ValueError("Expected a real registry digest")
    # No directory recursion, git archive, ambient config, or secret reads.
    # Refuse all existing paths, including symlinks and partial earlier outputs.
    output.mkdir(parents=True, exist_ok=False)
    template = (ROOT / "deploy/tinfoil/tinfoil-config.example.yml").read_text()
    start = template.index("    env:\n")
    end = template.index("    healthcheck:\n", start)
    environment = {
        **SETTINGS,
        "XDG_CACHE_HOME": "/tmp/aiab-cache",
        "XDG_DATA_HOME": "/tmp/aiab-data",
    }
    config = template[:start] + "    env:\n"
    config += "".join(f"      - {key}: {json.dumps(value)}\n" for key, value in environment.items())
    config += (
        "    secrets:\n      - TINFOIL_API_KEY\n"
        "      - ANTHROPIC_API_KEY\n      - AIAB_GATEWAY_TOKEN\n"
    )
    config += template[end:]
    config = config.replace(
        "ghcr.io/REPLACE_ORG/REPLACE_IMAGE@sha256:" + "0" * 64,
        image + "@" + (digest or "sha256:" + "0" * 64),
    )
    config = config[config.index("cvm-version:") :]
    manifest = expected_manifest(SETTINGS)
    approved_digest = policy_digest(manifest)
    (output / "tinfoil-config.yml").write_text(config)
    (output / "policy-settings.json").write_text(json.dumps(SETTINGS, indent=2) + "\n")
    (output / "policy-manifest.json").write_text(canonical_json(manifest) + "\n")
    (output / "policy-digest.txt").write_text(approved_digest + "\n")
    workflows = output / ".github/workflows"
    workflows.mkdir(parents=True)
    (workflows / "measure-release.yml").write_text(
        (ROOT / "deploy/tinfoil/workflows/measure-release.yml").read_text()
    )
    (output / "README.md").write_text(
        "# AIAB enclave release candidate\n\n"
        "This directory contains only measured deployment settings, a policy manifest, "
        "and a pinned measurement workflow. It is not a deployed or approved release.\n\n"
        f"Application image: `{image}@{digest or 'UNPUBLISHED'}`\n\n"
        f"Candidate policy digest: `{approved_digest}`\n\n"
        "Tinfoil release digest: **not yet measured or independently approved**.\n\n"
        "The application image contains executable AIAB source and classifier definitions. "
        "Making the image public discloses those files, even when the source GitHub repository "
        "remains private. Review that disclosure before publication.\n\n"
        "Repository secrets: TINFOIL_API_KEY, ANTHROPIC_API_KEY, AIAB_GATEWAY_TOKEN. "
        "Secret values never belong in this repository. Standard Tinfoil deployment secrets "
        "are accessible to Tinfoil infrastructure.\n"
    )
    return approved_digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--digest", help="Published registry sha256 digest; omit for a draft")
    args = parser.parse_args()
    try:
        digest = prepare(args.output, args.image, args.digest)
    except (ValueError, OSError) as error:
        raise SystemExit(f"Cannot prepare release: {error}") from None
    print(f"Local candidate: {args.output}\nCandidate policy digest: {digest}")


if __name__ == "__main__":
    main()
