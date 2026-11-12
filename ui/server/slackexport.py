"""The Slack export as an encrypted bundle in the repository.

The public-channel export (one JSON lines file per channel) is internal company content,
and the repository is public, so it is committed encrypted: ``ui/data/slack-export.enc``
is a tar of the ``*.jsonl`` files, zstd-compressed, sealed with AES-256-GCM under a key
that lives only in the servers' environment (AIAB_SLACK_EXPORT_KEY) and in 1Password. At
start the server opens the bundle into AIAB_LOGS_DIR/slack and the Logs page reads that
folder like any static copy. Nothing is fetched or updated later: a new scrape is a new
bundle, packed and committed by hand.

Pack a scrape (the key is read from AIAB_SLACK_EXPORT_KEY, or made with ``newkey``)::

    cd ui && uv run python -m server.slackexport newkey > ~/.aiab-slack.key
    export AIAB_SLACK_EXPORT_KEY=$(cat ~/.aiab-slack.key)
    uv run python -m server.slackexport pack ~/slack-export

File layout: an 8-byte magic ``AIABSLK1``, a 12-byte nonce, then the ciphertext and its
16-byte tag; the magic is also the authenticated associated data, so a bundle from another
layout is refused rather than misread.
"""

from __future__ import annotations

import argparse
import base64
import io
import os
import secrets
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

import zstandard
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"AIABSLK1"
NONCE_BYTES = 12
KEY_BYTES = 32


class BundleError(Exception):
    """The bundle or the key is not usable; the message says which."""


def new_key() -> str:
    """A fresh 256-bit key, base64 like the other keys in the environment."""
    return base64.b64encode(secrets.token_bytes(KEY_BYTES)).decode("ascii")


def _key_bytes(key: str, env: str = "AIAB_SLACK_EXPORT_KEY") -> bytes:
    try:
        raw = base64.b64decode(key.strip(), validate=True)
    except (ValueError, TypeError) as exc:
        raise BundleError(f"{env} is not base64") from exc
    if len(raw) != KEY_BYTES:
        raise BundleError(f"{env} must decode to {KEY_BYTES} bytes, not {len(raw)}")
    return raw


def pack(
    folder: Path, key: str, out: Path, magic: bytes = MAGIC, env: str = "AIAB_SLACK_EXPORT_KEY"
) -> list[str]:
    """Seal the ``*.jsonl`` files at the top of ``folder`` into ``out``; returns their names.
    The ``.md`` renderings next to them are not the data and stay out. ``magic`` and ``env``
    name the kind of bundle (the GitHub export reuses this with its own, server.githubexport)."""
    names = sorted(p.name for p in folder.glob("*.jsonl") if p.is_file())
    if not names:
        raise BundleError(f"no *.jsonl files in {folder}")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name in names:
            tar.add(folder / name, arcname=name)
    plain = zstandard.ZstdCompressor(level=19).compress(buf.getvalue())
    nonce = secrets.token_bytes(NONCE_BYTES)
    sealed = AESGCM(_key_bytes(key, env)).encrypt(nonce, plain, magic)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(magic + nonce + sealed)
    return names


def unpack(
    bundle: Path,
    key: str,
    dest: Path,
    magic: bytes = MAGIC,
    env: str = "AIAB_SLACK_EXPORT_KEY",
    what: str = "Slack export",
) -> list[str]:
    """Open ``bundle`` into ``dest`` (replaced whole, atomically); returns the file names.
    Only regular ``*.jsonl`` members without a path are taken out of the tar."""
    raw = _key_bytes(key, env)
    try:
        data = bundle.read_bytes()
    except OSError as exc:
        raise BundleError(f"cannot read {bundle}: {exc}") from exc
    if not data.startswith(magic) or len(data) < len(magic) + NONCE_BYTES + 16:
        raise BundleError(f"{bundle} is not a {what} bundle")
    nonce = data[len(magic) : len(magic) + NONCE_BYTES]
    try:
        plain = AESGCM(raw).decrypt(nonce, data[len(magic) + NONCE_BYTES :], magic)
    except InvalidTag as exc:
        raise BundleError(f"{env} does not open {bundle}") from exc
    try:
        tar_bytes = zstandard.ZstdDecompressor().decompress(plain, max_output_size=1 << 30)
    except zstandard.ZstdError as exc:
        raise BundleError(f"{bundle} opened but did not decompress: {exc}") from exc
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{dest.name}-", dir=dest.parent))
    names: list[str] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as tar:
            for member in tar.getmembers():
                if not member.isfile() or "/" in member.name or not member.name.endswith(".jsonl"):
                    continue
                tar.extract(member, staging, filter="data")
                names.append(member.name)
        if not names:
            raise BundleError(f"{bundle} holds no *.jsonl files")
        old = dest.with_name(dest.name + ".old")
        if old.exists():
            shutil.rmtree(old)
        if dest.exists():
            dest.rename(old)
        staging.rename(dest)
        if old.exists():
            shutil.rmtree(old)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return sorted(names)


def unpack_at_start(settings) -> list[str] | None:
    """Open the committed bundle into the Slack source's folder when the server holds the
    key and reads no folder of its own; None when there is nothing to do. Errors are
    reported, not raised: the server starts and Slack Logs shows empty."""
    source = settings.log_source("slack")
    if source.root is not None or not source.key:
        return None
    try:
        return unpack(settings.slack_bundle, source.key, source.path(settings))
    except BundleError as exc:
        print(f"warning: Slack Logs stays empty: {exc}")
        return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="pack or open the Slack export bundle")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("newkey", help="print a fresh key for AIAB_SLACK_EXPORT_KEY")
    p = sub.add_parser("pack", help="seal a folder's *.jsonl files into the bundle")
    p.add_argument("folder", type=Path)
    p.add_argument("--out", type=Path, default=None, help="default: the committed bundle path")
    u = sub.add_parser("unpack", help="open a bundle into a folder")
    u.add_argument("dest", type=Path)
    u.add_argument("--bundle", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.cmd == "newkey":
        print(new_key())
        return 0
    from server.config import load_settings

    settings = load_settings()
    key = os.environ.get("AIAB_SLACK_EXPORT_KEY", "").strip()
    if not key:
        print("set AIAB_SLACK_EXPORT_KEY (make one with: newkey)", file=sys.stderr)
        return 2
    try:
        if args.cmd == "pack":
            out = args.out or settings.slack_bundle
            names = pack(args.folder, key, out)
            print(f"packed {len(names)} channel file(s) into {out} ({out.stat().st_size} bytes)")
        else:
            names = unpack(args.bundle or settings.slack_bundle, key, args.dest)
            print(f"opened {len(names)} channel file(s) into {args.dest}")
    except BundleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
