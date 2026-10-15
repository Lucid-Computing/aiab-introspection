"""Verified TLS transport pinned to a reviewed release before sending any body."""

from __future__ import annotations

import asyncio
import secrets

from tinfoil import SecureClient

from aiab.audit.protocol import AuditError, decode


class FixedClient(SecureClient):
    def _rebuild_sync_transport(self):
        raise AuditError("model_unavailable", 503)

    async def _rebuild_async_transport(self):
        raise AuditError("model_unavailable", 503)


class PinnedConnection:
    def __init__(self, host: str, repo: str, release_digest: str):
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
                or "sha256:" + doc.release_digest != self.release_digest
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

    async def close(self):
        if self.client is not None:
            await self.client.aclose()
