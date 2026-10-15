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
    paths = ["__init__.py", "plan.py", "plan.json", "signatures/lab.sig", "signatures/auditor.sig"]
    tree = git("ls-tree", "-r", head, "--", "plan/prompts").decode().splitlines()
    for entry in tree:
        metadata, path = entry.split("\t", 1)
        if metadata.split()[0] != "100644" or not re.fullmatch(
            r"plan/prompts/[A-Za-z0-9_-]{1,128}\.txt", path
        ):
            raise ValueError("Invalid prompt path")
        paths.append(path.removeprefix("plan/"))
    if len(paths) > 37:
        raise ValueError("Too many inputs")
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        for path in paths:
            spec = f"{head}:plan/{path}"
            entry = git("ls-tree", head, "--", "plan/" + path).decode()
            if not entry.startswith("100644 blob ") or int(git("cat-file", "-s", spec)) > 65536:
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
