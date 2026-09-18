#!/usr/bin/env python3
"""Rotate the private Anthropic host credential from a hidden terminal prompt."""

from __future__ import annotations

import fcntl
import getpass
import os
import re
import resource
import secrets
import stat
import sys
import warnings
from contextlib import ExitStack

DESTINATION = "/etc/aiab-introspection/anthropic.secret"
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
STAGING_DIRECTORY = ".anthropic-rotation"
STAGING_NAME = re.compile(r"key-[0-9a-f]{32}\Z")


class RotationError(Exception):
    """Fixed, credential-free diagnostic safe to show to the operator."""


def private_directory(fd: int) -> None:
    info = os.fstat(fd)
    if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700):
        raise RotationError("Secret directories must be root:root with mode 0700.")


def credential_metadata(directory_fd: int) -> os.stat_result:
    info = os.stat("anthropic.secret", dir_fd=directory_fd, follow_symlinks=False)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (65532, 65532, 0o400)
    ):
        raise RotationError(
            "Existing credential must be a single-link regular 65532:65532/0400 file."
        )
    return info


def clean_staging(staging_fd: int) -> None:
    """Remove only our validated staging entries; never open their contents."""
    names = os.listdir(staging_fd)
    for name in names:
        info = os.stat(name, dir_fd=staging_fd, follow_symlinks=False)
        # A crash can occur before or after ownership is transferred.
        if (
            STAGING_NAME.fullmatch(name) is None
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or (info.st_uid, info.st_gid) not in {(0, 0), (65532, 65532)}
            or stat.S_IMODE(info.st_mode) != 0o400
            or info.st_size > 4097
        ):
            raise RotationError("Unsafe staging entry; refusing cleanup or rotation.")
    for name in names:
        os.unlink(name, dir_fd=staging_fd)
    os.fsync(staging_fd)


def read_key() -> bytes:
    # getpass otherwise falls back to echoed input when no terminal is available.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        value = getpass.getpass("New Anthropic API key (hidden): ")
    if not 1 <= len(value) <= 4096 or any(not 33 <= ord(c) <= 126 for c in value):
        raise RotationError("Key must contain 1–4096 printable ASCII non-space characters.")
    return value.encode("ascii")


def rotate() -> None:
    with ExitStack() as stack:

        def keep(fd: int) -> int:
            stack.callback(os.close, fd)
            return fd

        root_fd = keep(os.open("/", DIRECTORY_FLAGS))
        etc_fd = keep(os.open("etc", DIRECTORY_FLAGS, dir_fd=root_fd))
        for fd in (root_fd, etc_fd):
            info = os.fstat(fd)
            if info.st_uid != 0 or info.st_gid != 0 or info.st_mode & 0o022:
                raise RotationError(
                    "Host parent directories must be root:root and not writable by others."
                )
        directory_fd = keep(os.open("aiab-introspection", DIRECTORY_FLAGS, dir_fd=etc_fd))
        private_directory(directory_fd)
        # Serialize this helper, including stale cleanup and interactive input.
        fcntl.flock(directory_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        original = credential_metadata(directory_fd)
        try:
            os.mkdir(STAGING_DIRECTORY, mode=0o700, dir_fd=directory_fd)
            os.fsync(directory_fd)
        except FileExistsError:
            pass
        staging_fd = keep(os.open(STAGING_DIRECTORY, DIRECTORY_FLAGS, dir_fd=directory_fd))
        private_directory(staging_fd)
        clean_staging(staging_fd)
        key = read_key()
        name = "key-" + secrets.token_hex(16)
        temporary_fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o400,
            dir_fd=staging_fd,
        )
        try:
            with os.fdopen(temporary_fd, "wb") as output:
                output.write(key + b"\n")
                output.flush()
                os.fchown(output.fileno(), 65532, 65532)
                os.fchmod(output.fileno(), 0o400)
                info = os.fstat(output.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
                    != (65532, 65532, 0o400)
                    or info.st_size != len(key) + 1
                ):
                    raise RotationError("Staged credential metadata verification failed.")
                os.fsync(output.fileno())
            os.fsync(staging_fd)
            current = credential_metadata(directory_fd)
            if (current.st_dev, current.st_ino, current.st_ctime_ns) != (
                original.st_dev,
                original.st_ino,
                original.st_ctime_ns,
            ):
                raise RotationError(
                    "Existing credential changed during rotation; refusing replacement."
                )
            os.replace(name, "anthropic.secret", src_dir_fd=staging_fd, dst_dir_fd=directory_fd)
            os.fsync(directory_fd)
            os.fsync(staging_fd)
            credential_metadata(directory_fd)
        finally:
            try:
                os.unlink(name, dir_fd=staging_fd)
            except FileNotFoundError:
                pass
            os.fsync(staging_fd)


def main() -> None:
    # Reject even --help without echoing argv: an operator may accidentally
    # paste a credential as an argument. Only the hidden prompt accepts input.
    if len(sys.argv) != 1:
        raise SystemExit(
            "This helper accepts no arguments. Enter the key only at its hidden prompt."
        )
    if os.geteuid() != 0 or os.getegid() != 0:
        raise SystemExit("Run with sudo from an interactive terminal; root:root is required.")
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        rotate()
    except RotationError as exc:
        raise SystemExit(str(exc)) from None
    except (Exception, KeyboardInterrupt):
        # Never render exception details or input. Durability checks may fail
        # after replacement: do not delete or roll back the published credential.
        raise SystemExit(
            "Rotation did not complete cleanly. Check terminal availability, directory/file "
            "metadata, concurrent rotation, and filesystem support. Replacement may already "
            "have occurred; no credential contents were displayed."
        ) from None
    print("Rotated " + DESTINATION + " (owner 65532:65532, mode 0400).")
    print("Recreate the AIAB container with all active Compose overlays to load the new key.")


if __name__ == "__main__":
    main()
