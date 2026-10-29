"""Validate a plan-only PR using trusted base code without importing PR Python."""

import argparse
import re
import subprocess
import tempfile
from pathlib import Path

from aiab.audit.protocol import decode
from aiab.baked.bundle import load_bundle


def git(*args):
    return subprocess.check_output(["git", *args], timeout=60)


def check(base, head):
    if not all(re.fullmatch(r"[0-9a-f]{40}", s) for s in (base, head)):
        raise ValueError("Invalid revision")
    changes = git("diff", "--name-only", base, head).decode().splitlines()
    if not changes or any(not p.startswith("plan/") for p in changes):
        raise ValueError("Automatic approval accepts plan-only changes")
    trusted = decode(Path("static/aiab/baked/trust.json").read_bytes())
    paths = ["plan.json", "signatures/lab.sig", "signatures/auditor.sig"]
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        for path in paths:
            spec = f"{head}:plan/{path}"
            entry = git("ls-tree", head, "--", "plan/" + path).decode()
            if not entry.startswith("100644 blob ") or int(git("cat-file", "-s", spec)) > 262144:
                raise ValueError("Expected bounded regular file")
            dest = folder / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(git("show", spec))
        return load_bundle(folder, trusted).digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args()
    print(check(args.base, args.head))


if __name__ == "__main__":
    main()
