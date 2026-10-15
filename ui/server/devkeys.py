"""Development-only party keys in the box's format, plus a trust file that pins them.

``python -m server.devkeys DIR`` writes lab.key, auditor.key, logger.key (base64 raw
Ed25519 private keys, mode 0600) and trust.json (the public keys plus the model binding
copied from static/aiab/baked/trust.json). Real parties generate their own keys.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from server.config import REPO_ROOT

STATIC_TRUST = REPO_ROOT / "static" / "aiab" / "baked" / "trust.json"


def write_private(path: Path, key: Ed25519PrivateKey) -> None:
    raw = base64.b64encode(key.private_bytes_raw()).decode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(raw + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m server.devkeys")
    parser.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    args.directory.mkdir(parents=True, exist_ok=True)
    trust = json.loads(STATIC_TRUST.read_text(encoding="utf-8"))
    for role in ("lab", "auditor", "logger"):
        key = Ed25519PrivateKey.generate()
        write_private(args.directory / f"{role}.key", key)
        trust[f"{role}_key"] = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    out = args.directory / "trust.json"
    out.write_text(json.dumps(trust, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.directory}/{{lab,auditor,logger}}.key and {out}")
    print(f"start the UI with AIAB_TRUST_FILE={out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
