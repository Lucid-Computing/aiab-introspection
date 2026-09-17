"""Generate an expected manifest offline from reviewed, explicitly non-secret settings."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from aiab.classifier import TinfoilLLMClassifier
from aiab.config import Config, ConfigurationError
from aiab.reporter import build_manifest, canonical_json, policy_digest
from auditor.verify import VerificationError, strict_json

PUBLIC_SETTINGS = frozenset(
    {
        "AIAB_MODE",
        "AIAB_ID",
        "ENFORCEMENT_MODE",
        "BLOCK_THRESHOLD",
        "INFERENCE_URL",
        "LISTEN_PORT",
        "CORS_ALLOW_ORIGIN",
        "SAMPLE_RATE",
        "REPORT_PERIOD_SECONDS",
        "REPORT_HISTORY",
        "CLASSIFIER_MODEL",
        "CLASSIFIER_DEFINITION",
        "LOG_LEVEL",
    }
)


def expected_manifest(settings: Any) -> dict[str, Any]:
    if (
        not isinstance(settings, dict)
        or settings.keys() - PUBLIC_SETTINGS
        or any(not isinstance(value, str) for value in settings.values())
        or settings.get("AIAB_MODE") != "tinfoil"
    ):
        raise VerificationError("Expected only public string settings with AIAB_MODE=tinfoil")
    # Construction is network-free; never load a real key or use ambient environment.
    config = Config.from_env({**settings, "TINFOIL_API_KEY": "offline-policy-generation"})
    return build_manifest(config, TinfoilLLMClassifier(config))


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = expected_manifest(strict_json(args.settings.read_bytes()))
    except (VerificationError, ConfigurationError, OSError):
        raise SystemExit("Cannot generate policy: invalid public settings file") from None
    print(canonical_json(manifest))
    print(f"Policy digest: {policy_digest(manifest)}", file=sys.stderr)


if __name__ == "__main__":
    main()
