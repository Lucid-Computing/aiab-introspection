"""Generate per-boot report signing keys and ephemeral local TLS material in memory."""

from __future__ import annotations

import base64
import ipaddress
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.x509.oid import NameOID


class SigningKey:
    def __init__(self) -> None:
        self._key = ed25519.Ed25519PrivateKey.generate()

    @property
    def public_key(self) -> str:
        raw = self._key.public_key().public_bytes_raw()
        return base64.b64encode(raw).decode("ascii")

    def sign(self, message: str) -> str:
        return base64.b64encode(self._key.sign(message.encode("utf-8"))).decode("ascii")


@contextmanager
def ephemeral_tls_files() -> Iterator[tuple[str, str]]:
    """Linux memfd paths for OpenSSL; never create an ordinary private-key file.

    Python's SSLContext needs paths, so anonymous kernel-backed files bridge its API.
    Fail explicitly on systems without memfd/procfs rather than writing a temp key.
    These are volatile memory objects, not persistence or protection from swap/root.
    """
    if not hasattr(os, "memfd_create") or not os.path.isdir("/proc/self/fd"):
        raise RuntimeError("Local TLS requires Linux memfd and /proc/self/fd")
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(private, hashes.SHA256())
    )
    fds: list[int] = []
    try:
        for label, data in (
            ("aiab-cert", certificate.public_bytes(serialization.Encoding.PEM)),
            (
                "aiab-key",
                private.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                ),
            ),
        ):
            fd = os.memfd_create(label, flags=os.MFD_CLOEXEC)
            fds.append(fd)
            os.write(fd, data)
        yield (f"/proc/self/fd/{fds[0]}", f"/proc/self/fd/{fds[1]}")
    finally:
        for fd in fds:
            os.close(fd)
