"""Bind fetched artifacts to a verified enclave and independently approved measurements."""

from __future__ import annotations

import re
import secrets
import subprocess
from dataclasses import dataclass
from typing import Any

import httpx

from auditor.verify import VerificationError, strict_json, timestamp

CLI_TIMEOUT_SECONDS = 60
_HEX256 = re.compile(r"[0-9a-f]{64}")
_PREDICATE = "https://tinfoil.sh/predicate/"
_REGISTER_COUNTS = {
    _PREDICATE + "sev-snp-guest/v2": 1,
    _PREDICATE + "tdx-guest/v2": 5,
    _PREDICATE + "snp-tdx-multiplatform/v1": 3,
}
_AUDIT_PATHS = frozenset({"/aiab/pubkey", "/aiab/manifest", "/aiab/reports", "/aiab/health"})


def production_inputs(host: str, repo: str | None, digest: str | None, policy: str | None) -> None:
    """Reject missing approvals and ambiguous destinations before any network I/O."""
    if not repo or not digest or not policy:
        raise VerificationError(
            "Attested mode requires --repo and independently approved release and policy digests"
        )
    # The CLI itself adds :443. Accept only canonical DNS names, not URLs or ports.
    labels = host.split(".")
    if (
        len(host) > 253
        or len(labels) < 2
        or not any(character.isalpha() for character in labels[-1])
        or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in labels)
    ):
        raise VerificationError("Attested host must be a DNS hostname without scheme, port or path")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", repo):
        raise VerificationError("Attested repository must have owner/repo form")
    if not _HEX256.fullmatch(digest) or not _HEX256.fullmatch(policy):
        raise VerificationError(
            "Expected release and policy digests must be 64 lowercase hex digits"
        )


def _measurement(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.keys() != {"type", "registers"}:
        raise VerificationError("Incomplete or unsupported attestation measurement")
    kind, registers = value["type"], value["registers"]
    if (
        not isinstance(kind, str)
        or kind not in _REGISTER_COUNTS
        or not isinstance(registers, list)
        or len(registers) != _REGISTER_COUNTS[kind]
        or any(
            not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{96}", item)
            for item in registers
        )
    ):
        raise VerificationError("Incomplete or unsupported attestation measurement")
    return value


@dataclass(frozen=True)
class VerifiedAttestation:
    host: str
    repo: str
    # Tinfoil release/Sigstore subject digest from tinfoil.hash; NOT an OCI image digest.
    release_digest: str
    tls_public_key: str
    code_measurement: dict[str, Any]


def verify_attestation(host: str, repo: str, expected_digest: str) -> VerifiedAttestation:
    """Require the reviewed CLI's complete, successful, production verification record.

    Debug hardware policies are rejected inside the CLI verifier. Its current JSON
    has no debug boolean; unknown fields (including invented debug flags) fail closed.
    No CLI output or exceptions are forwarded to the user's terminal.
    """
    try:
        result = subprocess.run(
            ["tinfoil", "attestation", "verify", "-e", host, "-r", repo, "-j"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=CLI_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise VerificationError("Tinfoil attestation command unavailable or timed out") from None
    if result.returncode != 0:
        raise VerificationError("Tinfoil attestation command rejected the enclave")
    if len(result.stdout) > 1_048_576:
        raise VerificationError("Oversized Tinfoil verification output")
    record = strict_json(result.stdout)
    required = {"timestamp", "enclave", "repo", "digest", "measurements", "keys", "status"}
    if (
        not isinstance(record, dict)
        or not required <= record.keys()
        or record.keys() - required - {"nonce", "error"}
        or record["status"] != "ok"
        or record.get("error")
        or record["enclave"] != host
        or record["repo"] != repo
    ):
        raise VerificationError("Incomplete, debug, or unsuccessful Tinfoil verification record")
    timestamp(record["timestamp"])
    if record["digest"] != expected_digest:
        raise VerificationError("Release digest does not match independently approved digest")
    measurements, keys = record["measurements"], record["keys"]
    if (
        not isinstance(measurements, dict)
        or not {"sigstore", "enclave"} <= measurements.keys()
        or measurements.keys() - {"sigstore", "enclave", "cert"}
        or not isinstance(keys, dict)
        or not {"enclave", "connection"} <= keys.keys()
        or keys.keys() - {"enclave", "connection", "cert"}
        or not isinstance(keys["enclave"], str)
        or not _HEX256.fullmatch(keys["enclave"])
        or keys["enclave"] != keys["connection"]
    ):
        raise VerificationError("Incomplete attestation or TLS key binding")
    code = _measurement(measurements["sigstore"])
    enclave = _measurement(measurements["enclave"])
    try:
        from tinfoil.attestation.types import Measurement, PredicateType

        Measurement(PredicateType(code["type"]), code["registers"]).assert_equal(
            Measurement(PredicateType(enclave["type"]), enclave["registers"])
        )
    except Exception:
        raise VerificationError("Attested code and enclave measurements differ") from None
    return VerifiedAttestation(host, repo, expected_digest, keys["enclave"], code)


class AttestedClient:
    """Fetch only audit paths using the SDK's host-bound, TLS-pinned transport."""

    def __init__(self, evidence: VerifiedAttestation) -> None:
        self.evidence = evidence
        self._http: httpx.Client | None = None
        try:
            from tinfoil import SecureClient

            class FixedIdentityClient(SecureClient):
                # SDK 0.14.0 otherwise retries after reattesting a *new* key,
                # before our post-request approval check. Never send a secret
                # to that newly selected identity. A fresh audit is required.
                def _rebuild_sync_transport(self) -> httpx.BaseTransport:
                    raise VerificationError("TLS identity changed; fetch again")

            self._secure = FixedIdentityClient(
                enclave=evidence.host,
                repo=evidence.repo,
                transport="tls",
                user_cache_secret=secrets.token_hex(32),
            )
            # This SDK factory performs attestation and disables redirects. An
            # ordinary httpx.Client would not establish the required key linkage.
            self._http = self._secure.make_secure_http_client()
            self._check()
        except Exception:
            self.close()
            raise VerificationError("Attested audit transport could not be verified") from None

    def _check(self) -> None:
        doc = self._secure.get_verification_document()
        if (
            doc is None
            or doc.security_verified is not True
            or doc.config_repo != self.evidence.repo
            or doc.enclave_host != self.evidence.host
            or doc.release_digest != self.evidence.release_digest
            or doc.tls_public_key != self.evidence.tls_public_key
            or any(
                doc.steps[name].status != "success"
                for name in (
                    "fetch_digest",
                    "verify_code",
                    "verify_enclave",
                    "compare_measurements",
                )
            )
            or doc.code_measurement is None
            or doc.code_measurement.type.value != self.evidence.code_measurement["type"]
            or doc.code_measurement.registers != self.evidence.code_measurement["registers"]
        ):
            raise VerificationError("Attested release or TLS identity changed; fetch again")

    def get(self, path: str) -> httpx.Response:
        if path not in _AUDIT_PATHS:
            raise VerificationError("Unsupported attested audit path")
        try:
            self._check()
            assert self._http is not None
            response = self._http.get(
                f"https://{self.evidence.host}{path}", timeout=15, follow_redirects=False
            )
            # The SDK may reverify after a TLS key rotation. Reject any release
            # or key change rather than mixing evidence from distinct verifications.
            self._check()
            return response
        except Exception:
            raise VerificationError(
                "Attested audit request or identity verification failed"
            ) from None

    def close(self) -> None:
        if self._http is not None:
            self._http.close()

    def inference(self, method: str, path: str, body: bytes, token: str) -> httpx.Response:
        """Fixed inference routes, no ambient credentials, cookies, or redirects.

        The gateway must verify its independently pinned policy using this same
        client before invoking this method. The SDK pins TLS before sending HTTP.
        """
        if (method, path) not in {
            ("GET", "/v1/models"),
            ("POST", "/v1/chat/completions"),
        } or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
            raise VerificationError("Unsupported inference request")
        try:
            self._check()
            assert self._http is not None
            self._http.cookies.clear()
            with self._http.stream(
                method,
                f"https://{self.evidence.host}{path}",
                content=body,
                headers={"authorization": f"Bearer {token}", "content-type": "application/json"},
                timeout=60,
                follow_redirects=False,
            ) as response:
                # Never relay redirects or their bodies; a caller cannot use
                # the enclave as a credential-bearing arbitrary URL proxy.
                if 300 <= response.status_code < 400:
                    raise VerificationError("Redirect rejected")
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 2_097_152:
                        raise VerificationError("Oversized inference response")
                self._check()
                return httpx.Response(response.status_code, content=bytes(chunks))
        except Exception:
            raise VerificationError("Attested inference request failed") from None

    def __enter__(self) -> AttestedClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
