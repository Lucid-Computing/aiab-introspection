"""Verify a complete signed text export with an exact-byte SHA256 hash chain.

This is an explicit exporter contract, not a claim of compatibility with an
unspecified upstream WORM implementation. An upstream adapter must verify its
native checkpoints before the trusted logger signs this export statement.
"""

from __future__ import annotations

import base64
import hashlib

from aiab.audit.protocol import (
    DIGEST,
    SCOPE,
    AuditError,
    canonical,
    closed,
    digest,
    validate,
    verify_signature,
)

STATEMENT = closed(
    {
        "schema": {"const": "aiab-logger-export/v1"},
        "scope": SCOPE,
        "first_sequence": {"type": "integer", "minimum": 0},
        "record_count": {"type": "integer", "minimum": 1, "maximum": 10000},
        "previous_head": DIGEST,
        "head": DIGEST,
        "text_sha256": DIGEST,
        "complete": {"const": True},
    }
)
EVIDENCE = closed(
    {
        "statement": STATEMENT,
        "signature": {"type": "string", "maxLength": 128},
        "chunks": {
            "type": "array",
            "minItems": 1,
            "maxItems": 10000,
            "items": {"type": "string", "maxLength": 1400000},
        },
    }
)


def bytes_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def chain_head(previous: str, sequence: int, raw: bytes) -> str:
    # Framing and sequence prevent concatenation ambiguity, reorder and replay.
    return bytes_digest(
        b"AIAB/logger-chain/v1\x00"
        + bytes.fromhex(previous[7:])
        + sequence.to_bytes(8, "big")
        + len(raw).to_bytes(8, "big")
        + raw
    )


class SignedTextVerifier:
    def verify(self, text: str, evidence: dict, scope: dict) -> str:
        try:
            validate(evidence, EVIDENCE, "invalid_logger_evidence")
            statement = evidence["statement"]
            if statement["scope"] != scope:
                raise AuditError("scope_mismatch")
            verify_signature(scope["logger_key"], evidence["signature"], "logger-export", statement)
            if len(evidence["chunks"]) != statement["record_count"]:
                raise AuditError("invalid_logger_evidence")
            raw = bytearray()
            head = statement["previous_head"]
            for index, chunk in enumerate(evidence["chunks"]):
                decoded = base64.b64decode(chunk, validate=True)
                raw.extend(decoded)
                if len(raw) > 1000000:
                    raise AuditError("invalid_logger_evidence")
                head = chain_head(head, statement["first_sequence"] + index, decoded)
            if (
                head != statement["head"]
                or bytes(raw) != text.encode("utf-8")
                or bytes_digest(bytes(raw)) != statement["text_sha256"]
            ):
                raise AuditError("invalid_logger_evidence")
            return digest(
                {
                    "scope": scope,
                    "text_sha256": statement["text_sha256"],
                    "source_statement": digest(statement),
                }
            )
        except AuditError as exc:
            if exc.code == "scope_mismatch":
                raise
            raise AuditError("invalid_logger_evidence") from None
        except (ValueError, TypeError, OverflowError):
            raise AuditError("invalid_logger_evidence") from None


def export_statement(
    chunks: list[bytes], scope: dict, *, previous_head: str, first_sequence: int
) -> dict:
    """Build the logger-side commitment. The logger must establish completeness."""
    head = previous_head
    for index, chunk in enumerate(chunks):
        head = chain_head(head, first_sequence + index, chunk)
    statement = {
        "schema": "aiab-logger-export/v1",
        "scope": scope,
        "first_sequence": first_sequence,
        "record_count": len(chunks),
        "previous_head": previous_head,
        "head": head,
        "text_sha256": bytes_digest(b"".join(chunks)),
        "complete": True,
    }
    validate(statement, STATEMENT, "invalid_logger_evidence")
    canonical(statement)
    return statement
