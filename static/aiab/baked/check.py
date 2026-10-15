"""Read-only CI check of a signed bundle against trusted static keys/restrictions."""

import argparse
from pathlib import Path

from aiab.audit.protocol import AuditError, canonical, decode
from aiab.baked.bundle import load_bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--trust", type=Path, default=Path(__file__).with_name("trust.json"))
    args = parser.parse_args()
    try:
        bundle = load_bundle(args.folder, decode(args.trust.read_bytes()))
        print(
            canonical(
                {
                    "schema": "aiab-plan-build/v1",
                    "plan_digest": bundle.digest,
                    "plan_id": bundle.config["plan_id"],
                }
            ).decode()
        )
    except Exception as exc:
        raise SystemExit(exc.code if isinstance(exc, AuditError) else "invalid_bundle") from None


if __name__ == "__main__":
    main()
