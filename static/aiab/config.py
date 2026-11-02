"""Private credential loading shared by the signed-audit service and clients."""

import os
import stat
from pathlib import Path


class ConfigurationError(ValueError):
    """Invalid private credential (never include its value in the error)."""


def read_private_key(filename: str, setting: str) -> str:
    """Read once through directory descriptors, refusing symlinks in every component.

    Only root or the process UID may own the 0400/0600 regular file. Return only
    the key, never retain the path, and reflect neither value in errors.
    ``setting`` is a caller-supplied constant, never an environment value.
    """
    directory_fd = None
    try:
        path = Path(filename)
        if not path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
            raise ValueError
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        directory_fd = os.open(path.anchor, directory_flags)
        for component in path.parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        fd = os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=directory_fd,
        )
        with os.fdopen(fd, "rb") as secret:
            metadata = os.fstat(secret.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}
                or metadata.st_uid not in {0, os.geteuid()}
                or not 1 <= metadata.st_size <= 4097
            ):
                raise ValueError
            value = secret.read(4098).removesuffix(b"\n")
        if not 1 <= len(value) <= 4096 or any(byte < 33 or byte > 126 for byte in value):
            raise ValueError
        return value.decode("ascii")
    except (OSError, ValueError, UnicodeError):
        raise ConfigurationError(f"{setting} must be a private regular key file") from None
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
