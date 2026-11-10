"""Verified TLS transport pinned to a reviewed release before sending any body."""

from __future__ import annotations

import asyncio
import re
import secrets

from tinfoil import SecureClient

from aiab.audit.protocol import AuditError, decode


class FixedClient(SecureClient):
    def _rebuild_sync_transport(self):
        raise AuditError("model_unavailable", 503)

    async def _rebuild_async_transport(self):
        raise AuditError("model_unavailable", 503)


class PinnedConnection:
    """Attested TLS to one host. ``release_digest`` pins the exact measured release; None
    accepts the repository's current release, as verified by the SDK through Sigstore."""

    def __init__(self, host: str, repo: str, release_digest: str | None):
        self.host, self.repo, self.release_digest = host, repo, release_digest
        self.secure = None
        self.client = None
        self.key = None
        self.failed = False

    async def start(self):
        try:

            def initialize():
                secure = FixedClient(
                    enclave=self.host,
                    repo=self.repo,
                    transport="tls",
                    user_cache_secret=secrets.token_hex(32),
                )
                secure._user_cache_secret = ""
                return secure, secure.make_secure_async_http_client()

            self.secure, self.client = await asyncio.to_thread(initialize)
            self.check()
        except Exception:
            self.failed = True
            await self.close()
            raise AuditError("model_unavailable", 503) from None

    def check(self):
        try:
            doc = self.secure.get_verification_document()
            if (
                self.failed
                or doc.security_verified is not True
                or doc.config_repo != self.repo
                or doc.enclave_host != self.host
                or self.secure.enclave != self.host
                or not re.fullmatch(r"[0-9a-f]{64}", doc.release_digest or "")
                or (
                    self.release_digest is not None
                    and "sha256:" + doc.release_digest != self.release_digest
                )
            ):
                raise ValueError
            for name in ("fetch_digest", "verify_code", "verify_enclave", "compare_measurements"):
                if doc.steps[name].status != "success":
                    raise ValueError
            key = self.secure._ground_truth.public_key
            if self.key is not None and key != self.key:
                raise ValueError
            self.key = key
        except Exception:
            self.failed = True
            raise AuditError("model_unavailable", 503) from None

    async def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        token: str | None = None,
        timeout: float = 120,
    ):
        self.check()
        if not path.startswith("/") or ".." in path or "?" in path or "#" in path:
            raise AuditError("invalid_path")
        try:
            headers = {"content-type": "application/json"}
            if token:
                headers["authorization"] = "Bearer " + token
            self.client.cookies.clear()
            async with self.client.stream(
                method,
                "https://" + self.host + path,
                content=body,
                headers=headers,
                timeout=timeout,
                follow_redirects=False,
            ) as response:
                if 300 <= response.status_code < 400:
                    raise AuditError("model_unavailable", 503)
                self.check_response(response)
                result = bytearray()
                async for chunk in response.aiter_bytes():
                    result.extend(chunk)
                    if len(result) > 2_097_152:
                        raise AuditError("model_unavailable", 503)
                self.check()
                return response.status_code, decode(bytes(result))
        except Exception:
            self.failed = True
            raise AuditError("model_unavailable", 503) from None

    def check_response(self, response):
        """Further checks on a response's headers before its body is read."""

    @property
    def release(self) -> str | None:
        """The attested release digest, once verified."""
        doc = self.secure.get_verification_document() if self.secure is not None else None
        return "sha256:" + doc.release_digest if doc is not None and doc.release_digest else None

    async def close(self):
        if self.client is not None:
            await self.client.aclose()


ROUTER_REPO = "tinfoilsh/confidential-model-router"
ENCLAVE_HEADER = "tinfoil-enclave"


class RoutedConnection(PinnedConnection):
    """Attested TLS to Tinfoil's model router, which forwards each request to an enclave
    serving the named model and verifies that enclave itself.

    The router's code is its repository's current release (Sigstore-verified by the SDK);
    the router publishes releases every few days, so no single digest is pinned. The box
    cannot attest the model enclave behind it: it checks that the router names, for every
    answer, an enclave of the expected model, and records which enclaves served it.
    """

    def __init__(self, host: str, model_id: str):
        super().__init__(host, ROUTER_REPO, None)
        self.enclave = re.compile(
            re.escape(model_id) + r"-[a-z0-9-]+\.tinfoil\.containers\.tinfoil\.dev"
        )
        self.served: set[str] = set()

    def check_response(self, response):
        enclave = response.headers.get(ENCLAVE_HEADER, "")
        if response.status_code == 200 and not self.enclave.fullmatch(enclave):
            raise AuditError("model_unavailable", 503)
        if enclave and self.enclave.fullmatch(enclave):
            self.served.add(enclave)
