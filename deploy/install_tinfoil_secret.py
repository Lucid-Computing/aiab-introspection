#!/usr/bin/env python3
"""Install the repository's TINFOIL_API_KEY as a private host secret (Linux/root)."""

from __future__ import annotations

import argparse
import ctypes
import os
import re
import resource
import stat
from pathlib import Path

DESTINATION = "/etc/aiab-introspection/tinfoil.secret"
MAX_ENV_BYTES = 1024 * 1024


def read_key() -> bytes:
    """Parse one literal assignment; never source .env or expand shell expressions."""
    source = Path(__file__).resolve().parent.parent / ".env"
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Repository .env must be a regular file, not a symlink.")
        contents = stream.read(MAX_ENV_BYTES + 1)
    if len(contents) > MAX_ENV_BYTES:
        raise ValueError("Repository .env exceeds the 1 MiB input limit.")

    values = []
    for line in contents.split(b"\n"):
        line = line.removesuffix(b"\r")
        if not re.match(rb"[ \t]*(?:export[ \t]+)?TINFOIL_API_KEY\b", line):
            continue
        match = re.fullmatch(
            rb"[ \t]*(?:export[ \t]+)?TINFOIL_API_KEY[ \t]*=[ \t]*"
            rb"(?:'([^']*)'|\"([^\"]*)\"|([^ \t'\"]*))[ \t]*(?:[ \t]#.*)?",
            line,
        )
        if match is None:
            raise ValueError("Unsupported TINFOIL_API_KEY assignment in repository .env.")
        values.append(next(value for value in match.groups() if value is not None))
    if len(values) != 1:
        raise ValueError("Repository .env must contain exactly one TINFOIL_API_KEY assignment.")
    key = values[0]
    if not 1 <= len(key) <= 4096 or any(byte < 33 or byte > 126 for byte in key):
        raise ValueError("TINFOIL_API_KEY must contain 1–4096 printable ASCII non-space bytes.")
    return key


def install(key: bytes) -> None:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    etc_fd = os.open("/etc", directory_flags)
    try:
        etc_info = os.fstat(etc_fd)
        if etc_info.st_uid != 0 or etc_info.st_mode & 0o022:
            raise ValueError("/etc must be root-owned and not group/other writable.")
        try:
            os.mkdir("aiab-introspection", mode=0o700, dir_fd=etc_fd)
        except FileExistsError:
            pass
        directory_fd = os.open("aiab-introspection", directory_flags, dir_fd=etc_fd)
    finally:
        os.close(etc_fd)

    try:
        if os.fstat(directory_fd).st_uid != 0:
            raise ValueError("The destination directory must be owned by root.")
        os.fchmod(directory_fd, 0o700)
        # This preliminary check gives a useful error; linkat also refuses a
        # destination created concurrently, including a dangling symlink.
        try:
            os.stat("tinfoil.secret", dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("Destination already exists; refusing to replace it.")

        # O_TMPFILE leaves no directory entry to clean up, even after SIGKILL.
        # Fail closed on filesystems without support; do not fall back to /tmp.
        temporary_fd = os.open(
            ".", os.O_WRONLY | os.O_TMPFILE | os.O_CLOEXEC, 0o600, dir_fd=directory_fd
        )
        try:
            with os.fdopen(temporary_fd, "wb", closefd=False) as output:
                output.write(key + b"\n")
                output.flush()
                os.fchown(temporary_fd, 65532, 65532)
                os.fchmod(temporary_fd, 0o400)
                os.fsync(temporary_fd)
            libc = ctypes.CDLL(None, use_errno=True)
            linkat = libc.linkat
            linkat.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
            ]
            linkat.restype = ctypes.c_int
            # AT_EMPTY_PATH publishes the fully written inode atomically,
            # without replacing an existing name. Requires Linux/root.
            if linkat(temporary_fd, b"", directory_fd, b"tinfoil.secret", 0x1000) != 0:
                raise OSError(ctypes.get_errno(), "Atomic secret installation failed")
            os.fsync(directory_fd)
        finally:
            os.close(temporary_fd)
    finally:
        os.close(directory_fd)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Run this helper with sudo; it writes " + DESTINATION)
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        install(read_key())
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    except OSError:
        # Avoid OS exception details that might expose input. A directory fsync
        # failure can occur after publication; never delete a published secret.
        raise SystemExit(
            "Installation failed. Check source/destination permissions and Linux O_TMPFILE "
            "support. If the destination exists, it was not overwritten; inspect its metadata."
        ) from None
    print("Installed " + DESTINATION + " (owner 65532:65532, mode 0400).")


if __name__ == "__main__":
    main()
