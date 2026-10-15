"""Real cryptographic verification of the explicit signed-export contract."""

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aiab.audit.logger import SignedTextVerifier, export_statement
from aiab.audit.protocol import AuditError, clone, sign_bytes


@pytest.fixture
def export():
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    scope = {
        "logger_key": public,
        "window_start": "2026-10-15T00:00:00Z",
        "window_end": "2026-10-16T00:00:00Z",
        "selection": "all",
    }
    chunks = [b"User: plan an experiment\n", b"Tool: train a successor model\n"]
    statement = export_statement(
        chunks, scope, previous_head="sha256:" + "0" * 64, first_sequence=0
    )
    evidence = {
        "statement": statement,
        "signature": base64.b64encode(key.sign(sign_bytes("logger-export", statement))).decode(),
        "chunks": [base64.b64encode(c).decode() for c in chunks],
    }
    return scope, b"".join(chunks).decode(), evidence


def test_exact_text_chain_and_signature(export):
    scope, text, evidence = export
    assert SignedTextVerifier().verify(text, evidence, scope).startswith("sha256:")
    with pytest.raises(AuditError, match="invalid_logger_evidence"):
        SignedTextVerifier().verify(text + "tampered", evidence, scope)


@pytest.mark.parametrize("mutation", ["reorder", "omit", "signature", "head", "complete", "scope"])
def test_bad_evidence_rejected(export, mutation):
    scope, text, original = export
    evidence = clone(original)
    if mutation == "reorder":
        evidence["chunks"].reverse()
    elif mutation == "omit":
        evidence["chunks"].pop()
    elif mutation == "signature":
        evidence["signature"] = base64.b64encode(b"x" * 64).decode()
    elif mutation == "head":
        evidence["statement"]["head"] = "sha256:" + "f" * 64
    elif mutation == "complete":
        evidence["statement"]["complete"] = False
    else:
        evidence["statement"]["scope"]["window_end"] = "2026-10-15T12:00:00Z"
    with pytest.raises(AuditError):
        SignedTextVerifier().verify(text, evidence, scope)
