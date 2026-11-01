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

from aiab.audit.protocol import DATE, ID, AuditError, closed, decode, verify_signature
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


def bundle_files(folder: Path) -> dict[str, bytes]:
    path = folder / PLAN_FILE
    if (
        folder.is_symlink()
        or path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > MAX_PLAN_BYTES
    ):
        raise AuditError("invalid_bundle")
    return {PLAN_FILE: path.read_bytes()}


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


def load_bundle(folder: Path, trust: dict, *, signed: bool = True) -> Bundle:
    files = bundle_files(folder)
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
    if signed:
        for role in ("lab", "auditor"):
            path = folder / "signatures" / f"{role}.sig"
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 256:
                raise AuditError("invalid_signature")
            verify_signature(
                trust[role + "_key"],
                path.read_text().strip(),
                "plan-bundle",
                {"plan_digest": commitment},
            )
    return Bundle(files, commitment, analysis.config, analysis.plan, analysis.order, analysis)


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
