"""Byte-exact folder commitments, fixed restrictions and boot/CI verification.

The bundle is one file, ``plan.json``: the audit plan as a graph (plan_ir.py), in the
canonical JSON the builder saves. Tests, signatures and READMEs sit next to it and are not
part of the digest. The digest framing and the signature message are unchanged from the
earlier bundle format, so the signing tools and the CI check work as before.

Keys: the trust file holds one root public key per party (``lab_key``, ``auditor_key``).
A party does not sign plans with its root. It certifies a signing key with the root, a
short statement ``{"role", "signing_key"}`` signed under the kind ``signing-key``, and
signs plans with that key. A run carries each party's certificate next to its signature;
the box checks the certificate against the baked root, then the plan signature against
the certified key. So a party changes its signing key without a new image, and only a
root change goes through the trust file. The box keeps no state, so a certificate cannot
be revoked: a lost signing key stays valid until the root is rotated.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from aiab.audit.protocol import (
    DATE,
    ID,
    KEY,
    AuditError,
    canonical,
    closed,
    decode,
    sign_bytes,
    validate,
    verify_signature,
)
from aiab.baked.output import check_schema
from aiab.baked.plan_ir import LIMITS, PLAN_VERSION, PYTHON_MODULES, Analysis, check_plan

KINDS = ["request", "response", "tool_call", "tool_result"]
SCOPE = closed(
    {
        "logger_key_id": ID,
        "window_start": DATE,
        "window_end": DATE,
        "agents": {
            "type": "array",
            "items": ID,
            "minItems": 1,
            "uniqueItems": True,
        },
    }
)
PLAN_FILE = "plan.json"
PARTIES = ("lab", "auditor")
SIGNATURE = {"type": "string", "maxLength": 256}
CERTIFICATE_KIND = "signing-key"
MAX_CERTIFICATE_BYTES = 1024
# A party's root, baked into the box, says which key signs plans for it: the root's
# signature over {"role", "signing_key"}. The root itself never signs a plan.
CERTIFICATE = closed({"role": {"enum": list(PARTIES)}, "signing_key": KEY, "signature": SIGNATURE})
SIGNED_BUNDLE = closed(
    {
        "files": closed({PLAN_FILE: {"type": "string"}}),
        "signatures": closed({role: SIGNATURE for role in PARTIES}),
        "certificates": closed({role: CERTIFICATE for role in PARTIES}),
    }
)


def certificate_statement(role: str, signing_key: str) -> dict:
    return {"role": role, "signing_key": signing_key}


def make_certificate(root_private, role: str, signing_key: str) -> dict:
    """A certificate for ``signing_key`` as the party's ``role`` key, signed with the
    party's root private key (an Ed25519PrivateKey). Tools and tests; the box only checks."""
    import base64

    statement = certificate_statement(role, signing_key)
    signature = root_private.sign(sign_bytes(CERTIFICATE_KIND, statement))
    return {**statement, "signature": base64.b64encode(signature).decode()}


def key_bytes(key: str) -> bytes:
    """The 32 raw bytes of a key in its one canonical base64 spelling. Base64 has several
    spellings of the same bytes (unused trailing bits), and every key comparison below is
    on bytes, so a key spelled differently cannot pass as a different key."""
    import base64

    try:
        raw = base64.b64decode(key, validate=True)
    except (ValueError, TypeError):
        raise AuditError("invalid_certificate") from None
    if len(raw) != 32 or base64.b64encode(raw).decode() != key:
        raise AuditError("invalid_certificate")
    return raw


def certified_key(certificate, role: str, trust: dict) -> str:
    """The signing key a party's root certifies for ``role``, or ``invalid_certificate``.

    Refused: a malformed certificate, one for the other role, one the root did not sign,
    and one that names either party's root (a root certifies keys, it does not sign
    plans). Keys are compared as bytes, in canonical base64 only."""
    validate(certificate, CERTIFICATE, "invalid_certificate")
    root = trust[role + "_key"]
    signing_key = certificate["signing_key"]
    roots = {key_bytes(trust[party + "_key"]) for party in PARTIES}
    if certificate["role"] != role or key_bytes(signing_key) in roots:
        raise AuditError("invalid_certificate")
    try:
        verify_signature(
            root,
            certificate["signature"].strip(),
            CERTIFICATE_KIND,
            certificate_statement(role, signing_key),
        )
    except AuditError:
        raise AuditError("invalid_certificate") from None
    return signing_key


def certified_keys(certificates, trust: dict) -> dict[str, str]:
    """Both parties' certified signing keys; they must differ, as the roots must."""
    if not isinstance(certificates, dict) or set(certificates) != set(PARTIES):
        raise AuditError("invalid_certificate")
    keys = {role: certified_key(certificates[role], role, trust) for role in PARTIES}
    if len({key_bytes(k) for k in keys.values()}) != len(PARTIES):
        raise AuditError("invalid_certificate")
    return keys


def bundle_files(folder: Path) -> dict[str, bytes]:
    path = folder / PLAN_FILE
    if folder.is_symlink() or path.is_symlink() or not path.is_file():
        raise AuditError("invalid_bundle")
    return check_files_shape({PLAN_FILE: path.read_bytes()})


def check_files_shape(files: dict[str, bytes]) -> dict[str, bytes]:
    """Exactly the plan file, as the folder loader reads it."""
    if (
        not isinstance(files, dict)
        or set(files) != {PLAN_FILE}
        or not isinstance(files[PLAN_FILE], bytes)
    ):
        raise AuditError("invalid_bundle")
    return files


def encode_files(document) -> dict[str, bytes]:
    """Bundle files sent as JSON text (the plan file is UTF-8 text)."""
    if not isinstance(document, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in document.items()
    ):
        raise AuditError("invalid_bundle")
    try:
        return check_files_shape({name: text.encode() for name, text in document.items()})
    except UnicodeEncodeError:
        raise AuditError("invalid_bundle") from None


def file_digest(files: dict[str, bytes]) -> str:
    hashed = hashlib.sha256(b"AIAB/folder/v1\0")
    for name, raw in sorted(files.items()):
        path = name.encode()
        hashed.update(len(path).to_bytes(4, "big") + path + len(raw).to_bytes(8, "big") + raw)
    return "sha256:" + hashed.hexdigest()


@dataclass(frozen=True)
class Bundle:
    files: dict[str, bytes]
    digest: str
    config: dict  # plan_id, limits, output_schema and the optional input_scope
    plan: dict  # the checked plan document
    order: tuple[str, ...]  # node ids, every node after its inputs
    analysis: Analysis


def check_bundle(files: dict[str, bytes], trust: dict) -> Bundle:
    """Static restrictions over the bundle files; no signature check (see verify_bundle)."""
    files = check_files_shape(files)
    raw = decode(files[PLAN_FILE])
    if len({trust[k] for k in ("lab_key", "auditor_key", "logger_key")}) != 3:
        raise AuditError("invalid_trust_roots")
    analysis = check_plan(raw, trust["model"])  # PlanError, code invalid_plan
    scope = analysis.input_scope
    if scope and datetime.fromisoformat(scope["window_start"]) >= datetime.fromisoformat(
        scope["window_end"]
    ):
        raise AuditError("invalid_scope")
    check_schema(analysis.output_schema)
    commitment = file_digest(files)
    return Bundle(files, commitment, analysis.config, analysis.plan, analysis.order, analysis)


def verify_bundle(bundle: Bundle, signatures: dict, certificates: dict, trust: dict) -> None:
    """Both parties' signatures over the digest, each with the key that party's baked root
    certifies. The certificates are checked first: a plan signed by an uncertified key, or
    by a root itself, is ``invalid_certificate``, not a signature that merely fails."""
    keys = certified_keys(certificates, trust)
    if not isinstance(signatures, dict) or set(signatures) != set(PARTIES):
        raise AuditError("invalid_signature")
    for role in PARTIES:
        signature = signatures[role]
        if not isinstance(signature, str) or len(signature) > 256:
            raise AuditError("invalid_signature")
        verify_signature(
            keys[role], signature.strip(), "plan-bundle", {"plan_digest": bundle.digest}
        )


def submitted_bundle(document, trust: dict) -> Bundle:
    """A bundle sent with a run: ``{"files": {"plan.json": text}, "signatures": {lab, auditor},
    "certificates": {lab, auditor}}``.

    The same checks as a folder, inside the measured image, against the same baked trust
    roots. The bytes the digest covers are the UTF-8 encoding of the text sent, so a party
    signs exactly what the box runs. The box keeps nothing of it after the run.
    """
    validate(document, SIGNED_BUNDLE, "invalid_bundle")
    bundle = check_bundle(encode_files(document["files"]), trust)
    verify_bundle(bundle, document["signatures"], document["certificates"], trust)
    return bundle


def read_signatures(folder: Path) -> tuple[dict[str, str], dict[str, dict]]:
    """``signatures/<role>.sig`` (the base64 line) and ``signatures/<role>.cert`` (the
    certificate as JSON) for both parties; a missing or oversized file is a refusal."""
    signatures, certificates = {}, {}
    for role in PARTIES:
        path = folder / "signatures" / f"{role}.sig"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 256:
            raise AuditError("invalid_signature")
        signatures[role] = path.read_text()
        path = folder / "signatures" / f"{role}.cert"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_CERTIFICATE_BYTES:
            raise AuditError("invalid_certificate")
        try:
            certificates[role] = decode(path.read_bytes())
        except AuditError:
            raise AuditError("invalid_certificate") from None
    return signatures, certificates


def write_certificate(path: Path, certificate: dict) -> None:
    path.write_bytes(canonical(certificate) + b"\n")


def load_bundle(folder: Path, trust: dict, *, signed: bool = True) -> Bundle:
    bundle = check_bundle(bundle_files(folder), trust)
    if signed:
        signatures, certificates = read_signatures(folder)
        verify_bundle(bundle, signatures, certificates, trust)
    return bundle


def restrictions(trust):
    return {
        "version": "0.2.0",
        "plan_format": PLAN_VERSION,
        "python_imports": list(PYTHON_MODULES),
        "hardware": {"tee": "Tinfoil confidential VM", "gpu": "separately attested inference"},
        "models": [trust["model"]],
        "limits": LIMITS,
        "input_serialization_version": "aiab-records/v1",
        "output_schema_subset": "bounded-json/v1",
        "sandbox": "linux-x86_64-seccomp-worker/v1",
    }
