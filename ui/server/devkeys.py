"""Development-only party keys in the box's format, plus a trust file that pins them.

``python -m server.devkeys DIR`` writes lab.key, auditor.key, logger.key (base64 raw
Ed25519 private keys, mode 0600), a .pem copy of each for openssl, and trust.json (the
public keys plus the model binding copied from static/aiab/baked/trust.json). Running it
again keeps existing keys and only adds what is missing. Real parties make their own keys.
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


def load_or_make(path: Path) -> tuple[Ed25519PrivateKey, str]:
    """Reuse <role>.key when it exists (so re-running keeps the keys already pinned),
    otherwise make one. Either way a PEM copy in <role>.pem exists afterwards for openssl."""
    from cryptography.hazmat.primitives import serialization

    if path.exists():
        raw = base64.b64decode(path.read_text(encoding="utf-8").strip(), validate=True)
        key, note = Ed25519PrivateKey.from_private_bytes(raw), "kept"
    else:
        key, note = Ed25519PrivateKey.generate(), "new"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(base64.b64encode(key.private_bytes_raw()).decode() + "\n")
    pem_path = path.with_suffix(".pem")
    if not pem_path.exists():
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        fd = os.open(pem_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(pem)
        note += ", .pem written"
    return key, note


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m server.devkeys")
    parser.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    args.directory.mkdir(parents=True, exist_ok=True)
    trust = json.loads(STATIC_TRUST.read_text(encoding="utf-8"))
    for role in ("lab", "auditor", "logger"):
        key, note = load_or_make(args.directory / f"{role}.key")
        trust[f"{role}_key"] = base64.b64encode(key.public_key().public_bytes_raw()).decode()
        print(f"{role}.key: {note}")
    out = args.directory / "trust.json"
    out.write_text(json.dumps(trust, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(f"start the UI with AIAB_TRUST_FILE={out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
