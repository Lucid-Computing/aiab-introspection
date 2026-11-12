"""Records sealed to the box: the logger encrypts each record's content to a key only the
box holds, so the UI server in between stores and forwards what it cannot read.

The box makes the key pair at every start (or reads the one Tinfoil mounted when the measured
config declares an attested key) and publishes the public half as ``encryption_key`` in
``GET /aiab/pubkey``, next to the report key. The logger fetches it over the pinned TLS
connection the Tinfoil SDK opens, so only the measured code can have sent it. HPKE (RFC 9180)
with one fixed suite on both sides; the plaintext fields of a sealed record are its metadata,
which the box checks against the plan's scope before it decrypts anything.
"""

from __future__ import annotations

import base64
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from pyhpke import AEADId, CipherSuite, KDFId, KEMId, KEMKey

from aiab.audit.protocol import AuditError, canonical

SUITE = CipherSuite.new(KEMId.DHKEM_X25519_HKDF_SHA256, KDFId.HKDF_SHA256, AEADId.AES256_GCM)
SCHEME = "hpke-x25519-hkdf-sha256-aes256gcm/v1"
# Where Tinfoil's attested-keys feature mounts a key it generated (cvmimage >= 0.14.17, when
# tinfoil-config.yml declares one). Not used until a client SDK can verify such a key; until
# then the box makes its own key below and the file does not exist.
TINFOIL_KEY = Path("/run/tinfoil/keys/aiab-records/private_key.pem")
# The record fields the ciphertext is bound to: a record moved to another position, or sealed
# for another boot of the box, does not open.
BOUND = ("stream", "batch", "offset")


def record_aad(record: dict, boot_id: str) -> bytes:
    return canonical({**{k: record[k] for k in BOUND}, "boot_id": boot_id})


def seal_record(record: dict, encryption_key: str, boot_id: str) -> dict:
    """The record as the logger sends it: metadata in the clear, content sealed to the box."""
    public = SUITE.kem.deserialize_public_key(base64.b64decode(encryption_key, validate=True))
    enc, context = SUITE.create_sender_context(public)
    ciphertext = context.seal(record["content"].encode(), record_aad(record, boot_id))
    return {
        **record,
        "boot_id": boot_id,
        "enc": base64.b64encode(enc).decode(),
        "content": base64.b64encode(ciphertext).decode(),
    }


class RecordDecryptor:
    def __init__(self):
        if TINFOIL_KEY.exists():
            self._key = KEMKey.from_pem(TINFOIL_KEY.read_text())
        else:
            self._key = SUITE.kem.derive_key_pair(secrets.token_bytes(32)).private_key
        public = X25519PrivateKey.from_private_bytes(self._key.to_private_bytes()).public_key()
        self.public_key = base64.b64encode(public.public_bytes_raw()).decode()

    def unseal(self, records: list[dict], boot_id: str) -> list[dict]:
        """The records with their content in the clear. Plaintext records pass through.

        A record sealed for another boot answers stale_key: the box restarted since the
        logger fetched the key, and the caller exports again. Anything else that does not
        open (changed ciphertext, moved record, wrong key) is an invalid submission."""
        out = []
        for record in records:
            if "enc" not in record and "boot_id" not in record:
                out.append(record)
                continue
            if "enc" not in record or "boot_id" not in record:
                raise AuditError("invalid_submission")
            if record["boot_id"] != boot_id:
                raise AuditError("stale_key", 409)
            try:
                enc = base64.b64decode(record["enc"], validate=True)
                ciphertext = base64.b64decode(record["content"], validate=True)
                context = SUITE.create_recipient_context(enc, self._key)
                content = context.open(ciphertext, record_aad(record, boot_id)).decode()
            except Exception:  # bad base64, wrong key, moved record, not UTF-8: all one answer
                raise AuditError("invalid_submission") from None
            plain = {k: v for k, v in record.items() if k not in ("enc", "boot_id")}
            plain["content"] = content
            out.append(plain)
        return out
