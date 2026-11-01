"""Byte-exact folder commitments, fixed restrictions and boot/CI verification.

The bundle is one file, ``plan.json``: the audit plan as a graph (plan_ir.py), in the
canonical JSON the builder saves. Tests, signatures and READMEs sit next to it and are not
part of the digest. The digest framing and the signature message are unchanged from the
earlier bundle format, so the signing tools and the CI check work as before.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from aiab.audit.protocol import (
    DATE,
    ID,
    AuditError,
    closed,
    decode,
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
            "maxItems": 100,
            "uniqueItems": True,
        },
    }
)
PLAN_FILE = "plan.json"
MAX_PLAN_BYTES = 262144
SIGNED_BUNDLE = closed(
    {
        "files": closed({PLAN_FILE: {"type": "string", "maxLength": MAX_PLAN_BYTES}}),
        "signatures": closed(
            {role: {"type": "string", "maxLength": 256} for role in ("lab", "auditor")}
        ),
    }
)


def bundle_files(folder: Path) -> dict[str, bytes]:
    path = folder / PLAN_FILE
    if (
        folder.is_symlink()
        or path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > MAX_PLAN_BYTES
    ):
        raise AuditError("invalid_bundle")
    return check_files_shape({PLAN_FILE: path.read_bytes()})


def check_files_shape(files: dict[str, bytes]) -> dict[str, bytes]:
    """Exactly the plan file, within the same bound as the folder loader."""
    if (
        not isinstance(files, dict)
        or set(files) != {PLAN_FILE}
        or not isinstance(files[PLAN_FILE], bytes)
        or len(files[PLAN_FILE]) > MAX_PLAN_BYTES
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


def verify_bundle(bundle: Bundle, signatures: dict, trust: dict) -> None:
    """Both parties' signatures over the digest, against the static trust roots only."""
    if not isinstance(signatures, dict) or set(signatures) != {"lab", "auditor"}:
        raise AuditError("invalid_signature")
    for role in ("lab", "auditor"):
        signature = signatures[role]
        if not isinstance(signature, str) or len(signature) > 256:
            raise AuditError("invalid_signature")
        verify_signature(
            trust[role + "_key"], signature.strip(), "plan-bundle", {"plan_digest": bundle.digest}
        )


def submitted_bundle(document, trust: dict) -> Bundle:
    """A bundle sent with a run: ``{"files": {"plan.json": text}, "signatures": {lab, auditor}}``.

    The same checks as a folder, inside the measured image, against the same baked trust
    roots. The bytes the digest covers are the UTF-8 encoding of the text sent, so a party
    signs exactly what the box runs. The box keeps nothing of it after the run.
    """
    validate(document, SIGNED_BUNDLE, "invalid_bundle")
    bundle = check_bundle(encode_files(document["files"]), trust)
    verify_bundle(bundle, document["signatures"], trust)
    return bundle


def load_bundle(folder: Path, trust: dict, *, signed: bool = True) -> Bundle:
    bundle = check_bundle(bundle_files(folder), trust)
    if signed:
        signatures = {}
        for role in ("lab", "auditor"):
            path = folder / "signatures" / f"{role}.sig"
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 256:
                raise AuditError("invalid_signature")
            signatures[role] = path.read_text()
        verify_bundle(bundle, signatures, trust)
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
