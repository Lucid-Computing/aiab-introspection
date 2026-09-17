"""Reject bytecode and application Python caches in every layer of an OCI export."""

from __future__ import annotations

import argparse
import json
import re
import tarfile
from pathlib import PurePosixPath
from typing import Any, BinaryIO


def _member(archive: tarfile.TarFile, name: str) -> BinaryIO:
    member = archive.getmember(name)
    if not member.isfile():
        raise ValueError("Expected a regular OCI archive member")
    content = archive.extractfile(member)
    if content is None:
        raise ValueError("Missing OCI archive member")
    return content


def _blob(archive: tarfile.TarFile, descriptor: dict[str, Any]) -> BinaryIO:
    digest = descriptor["digest"]
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Unsupported OCI blob digest")
    return _member(archive, "blobs/sha256/" + digest.removeprefix("sha256:"))


def inspect_oci(path: str) -> int:
    """Read archive members without extracting files to the host filesystem."""
    count = 0
    with tarfile.open(path, "r:*") as archive:
        index = json.load(_member(archive, "index.json"))
        if len(index["manifests"]) != 1:
            raise ValueError("Expected exactly one OCI image manifest")
        descriptor = index["manifests"][0]
        if descriptor["mediaType"] != "application/vnd.oci.image.manifest.v1+json":
            raise ValueError("Expected a direct OCI image manifest")
        manifest = json.load(_blob(archive, descriptor))
        if not manifest["layers"]:
            raise ValueError("Expected image layers")
        for layer in manifest["layers"]:
            with tarfile.open(fileobj=_blob(archive, layer), mode="r|*") as contents:
                for member in contents:
                    path_parts = PurePosixPath(member.name).parts
                    # The pinned Python base ships empty standard-library cache
                    # directories. Any contents in them, or application caches
                    # even when empty, are forbidden.
                    empty_base_cache = (
                        member.isdir()
                        and path_parts[:4] == ("usr", "local", "lib", "python3.12")
                        and path_parts[-1] == "__pycache__"
                    )
                    if member.name.endswith((".pyc", ".pyo")) or (
                        "__pycache__" in path_parts and not empty_base_cache
                    ):
                        raise ValueError("Forbidden Python cache or bytecode in OCI layer")
                    count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    args = parser.parse_args()
    try:
        count = inspect_oci(args.archive)
    except (ValueError, KeyError, TypeError, OSError, tarfile.TarError):
        raise SystemExit("FAIL: OCI export invalid or contains Python cache/bytecode") from None
    print(f"PASS: inspected {count} OCI layer entries; no application __pycache__, .pyc or .pyo")


if __name__ == "__main__":
    main()
