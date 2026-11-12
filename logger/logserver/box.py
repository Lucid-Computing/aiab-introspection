"""The box records are sealed to, and its current key.

Which box is this server's own setting (BOX_HOST, BOX_REPO), never the caller's choice: a
caller who could name the key could have the logs sealed to a key of its own and read them.
The key is fetched the only way it can be trusted: the Tinfoil SDK checks the chip's report
against the config repo, pins the connection to the TLS key that report names, and
``GET /aiab/pubkey`` over that connection can then only be answered by the measured box.
The box makes a new key at every start, so the key is asked for again at every export.
"""

from __future__ import annotations

import base64
import threading

from aiab.baked.sealing import SCHEME, seal_record


class BoxError(RuntimeError):
    pass


def attested_client(host: str, repo: str):
    """An HTTP client that can only reach the measured box at ``host``."""
    from tinfoil import SecureClient

    secure = SecureClient(enclave=host, repo=repo, transport="tls")
    client = secure.make_secure_http_client()
    doc = secure.get_verification_document()
    if doc.security_verified is not True or doc.config_repo != repo or doc.enclave_host != host:
        raise BoxError("the box's attestation does not name this host and config repo")
    return client


class Box:
    def __init__(self, host: str, repo: str, connect=attested_client):
        self.host, self.repo = host, repo
        self._connect = connect
        self._client = None
        self._lock = threading.Lock()

    def identity(self) -> dict:
        """The box's ``encryption_key`` and ``boot_id``, now. The pinned connection is kept
        between calls; when it fails (the box restarted, so its TLS key changed) the box is
        attested again, once."""
        with self._lock:
            error: Exception | None = None
            for _ in range(2):
                try:
                    if self._client is None:
                        self._client = self._connect(self.host, self.repo)
                    response = self._client.get(f"https://{self.host}/aiab/pubkey", timeout=30)
                    response.raise_for_status()
                    return _checked(response.json())
                except BoxError:
                    self._client = None
                    raise
                except Exception as exc:
                    self._client = None
                    error = exc
            raise BoxError(f"the box could not be attested or did not answer: {error}")

    def seal(self, records: list[dict]) -> dict:
        """The records with each content sealed to the box's current key."""
        identity = self.identity()
        return {
            "scheme": SCHEME,
            "box": self.host,
            "boot_id": identity["boot_id"],
            "records": [
                seal_record(r, identity["encryption_key"], identity["boot_id"]) for r in records
            ],
        }


def _checked(identity) -> dict:
    try:
        key, boot = identity["encryption_key"], identity["boot_id"]
        if len(base64.b64decode(key, validate=True)) != 32 or not isinstance(boot, str) or not boot:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise BoxError("the box publishes no encryption key") from None
    return {"encryption_key": key, "boot_id": boot}
