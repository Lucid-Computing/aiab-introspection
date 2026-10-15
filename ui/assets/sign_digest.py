#!/usr/bin/env python3
"""Sign an AIAB plan digest with your own key, or recompute a digest from plan.zip.

Needs Python 3.10+ and the "cryptography" package (pip install cryptography).

  python3 sign_digest.py sign --digest sha256:... --key /path/to/lab.key
      prints the signature to paste into the agreement website

  python3 sign_digest.py digest plan.zip
      prints the digest of a downloaded plan bundle, to compare with the website

The key file holds one line: the base64 of a raw 32-byte Ed25519 private key.
The signed bytes are exactly what the box verifies:
  "AIAB/plan-bundle/v1\\0" + '{"plan_digest":"sha256:..."}'
"""

import argparse
import base64
import hashlib
import re
import sys
import zipfile


def folder_digest(files: dict[str, bytes]) -> str:
    hashed = hashlib.sha256(b"AIAB/folder/v1\0")
    for name, raw in sorted(files.items()):
        path = name.encode()
        hashed.update(len(path).to_bytes(4, "big") + path + len(raw).to_bytes(8, "big") + raw)
    return "sha256:" + hashed.hexdigest()


def sign(digest: str, key_path: str) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        sys.exit("the digest must look like sha256:<64 hex digits>")
    with open(key_path, encoding="utf-8") as handle:
        raw = base64.b64decode(handle.read().strip(), validate=True)
    message = b"AIAB/plan-bundle/v1\0" + f'{{"plan_digest":"{digest}"}}'.encode()
    signature = Ed25519PrivateKey.from_private_bytes(raw).sign(message)
    return base64.b64encode(signature).decode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    s = commands.add_parser("sign")
    s.add_argument("--digest", required=True)
    s.add_argument("--key", required=True)
    d = commands.add_parser("digest")
    d.add_argument("bundle")
    args = parser.parse_args()
    if args.command == "sign":
        print(sign(args.digest, args.key))
    else:
        with zipfile.ZipFile(args.bundle) as archive:
            files = {n: archive.read(n) for n in archive.namelist() if not n.endswith("/")}
        print(folder_digest(files))


if __name__ == "__main__":
    main()
