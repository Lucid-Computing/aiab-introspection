"""Byte-exact folder commitments, fixed restrictions and boot/CI verification."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from aiab.audit.protocol import (
    DATE,
    DIGEST,
    ID,
    TEXT,
    AuditError,
    closed,
    decode,
    validate,
    verify_signature,
)
from aiab.baked.output import check_schema

LIMITS = {
    "max_input_utf8_bytes": 1000000,
    "max_records_per_run": 2000,
    "max_logs_per_query": 32,
    "max_model_calls_per_run": 64,
    "max_runtime_seconds_per_run": 300,
    "max_result_bytes": 4096,
}
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
PLAN = closed(
    {
        "schema": {"const": "aiab-baked-plan/v1"},
        "plan_id": ID,
        "restrictions_version": {"const": "0.1.0"},
        "parties": closed({"lab": ID, "auditor": ID}),
        "input_scope": SCOPE,
        "filter": closed(
            {
                "keywords": {"type": "array", "items": TEXT, "maxItems": 32},
                "kinds": {
                    "type": "array",
                    "items": {"enum": KINDS},
                    "uniqueItems": True,
                    "minItems": 1,
                },
            }
        ),
        "window": {"type": "integer", "minimum": 1, "maximum": 32},
        "model": closed(
            {
                "id": TEXT,
                "artifact_digest": DIGEST,
                "temperature": {"const": 0},
                "max_output_tokens": {"type": "integer", "minimum": 32, "maximum": 2048},
            }
        ),
        "classification": closed(
            {
                "problem": TEXT,
                "positive_examples": {
                    "type": "array",
                    "items": TEXT,
                    "minItems": 1,
                    "maxItems": 32,
                },
                "negative_examples": {
                    "type": "array",
                    "items": TEXT,
                    "minItems": 1,
                    "maxItems": 32,
                },
                "prompt_id": ID,
                "threshold": {"type": "number", "minimum": 0, "maximum": 1},
                "evaluation": closed({"test_set_digest": DIGEST, "results_digest": DIGEST}),
            }
        ),
        "limits": closed(
            {k: {"type": "integer", "minimum": 1, "maximum": v} for k, v in LIMITS.items()}
        ),
        "output_schema": {"type": "object"},
    }
)
# Optional blocks: "filter" (see OPTIONAL_FUNCTIONS) and "input_scope" (input is chosen at run
# time; a plan may still pin agents and a window, and the box then enforces them).
PLAN["required"] = [k for k in PLAN["required"] if k not in ("filter", "input_scope")]
FUNCTIONS = {"filter_logs", "build_query", "classify", "metric", "emit_report", "appeal"}
# filter_logs is optional: input selection moved to run time,
# so plans without it skip that step.
OPTIONAL_FUNCTIONS = {"filter_logs"}
IMPORTS = {
    "__future__": ["annotations"],
    "typing": ["Any", "Literal", "Mapping", "Sequence", "Iterable", "Optional", "Union"],
    "dataclasses": ["asdict", "astuple", "dataclass", "field", "replace"],
    "aiab.plan_api": [
        "LogRecord",
        "QueryInput",
        "ClassifierResult",
        "Verdict",
        "FilterRules",
        "run_llm",
    ],
}


def check_source(source: str):
    try:
        tree = ast.parse(source)
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        names = {n.name for n in functions}
        if names not in (FUNCTIONS, FUNCTIONS - OPTIONAL_FUNCTIONS) or len(functions) != len(names):
            raise ValueError
        for node in tree.body:
            if (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                continue
            if not isinstance(node, (ast.FunctionDef, ast.Import, ast.ImportFrom)):
                raise ValueError
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                # Whole modules expose unrelated internals (e.g. typing.sys).
                raise ValueError
            if isinstance(node, ast.ImportFrom):
                if (
                    node.level
                    or node.module not in IMPORTS
                    or any(n.name not in IMPORTS[node.module] for n in node.names)
                ):
                    raise ValueError
            if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
                raise ValueError
            if isinstance(node, ast.Name) and (
                node.id.startswith("__")
                or node.id
                in {
                    "eval",
                    "exec",
                    "compile",
                    "open",
                    "globals",
                    "locals",
                    "vars",
                    "getattr",
                    "setattr",
                    "delattr",
                    "breakpoint",
                    "input",
                }
            ):
                raise ValueError
            if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.Global, ast.Nonlocal)):
                raise ValueError
            if isinstance(node, ast.FunctionDef) and node.decorator_list:
                raise ValueError
    except (ValueError, SyntaxError, RecursionError):
        raise AuditError("invalid_plan_source") from None


def bundle_files(folder: Path) -> dict[str, bytes]:
    files = {}
    for path in [
        folder / "__init__.py",
        folder / "plan.py",
        folder / "plan.json",
        *sorted((folder / "prompts").glob("*.txt")),
    ]:
        if (
            path.is_symlink()
            or not path.is_file()
            or any(p.is_symlink() for p in [folder, folder / "prompts"])
        ):
            raise AuditError("invalid_bundle")
        if path.stat().st_size > 65536:
            raise AuditError("invalid_bundle")
        files[path.relative_to(folder).as_posix()] = path.read_bytes()
    if not 4 <= len(files) <= 35 or sum(map(len, files.values())) > 262144:
        raise AuditError("invalid_bundle")
    return files


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
    config: dict

    @property
    def policy(self):
        cfg = self.config["classification"]
        return {**cfg, "prompt": self.files[f"prompts/{cfg['prompt_id']}.txt"].decode()}


def load_bundle(folder: Path, trust: dict, *, signed: bool = True) -> Bundle:
    files = bundle_files(folder)
    cfg = decode(files["plan.json"])
    validate(cfg, PLAN)
    if files["__init__.py"].decode().strip() not in {
        "",
        '"""The dynamic, jointly signed audit plan."""',
    }:
        raise AuditError("invalid_bundle_init")
    if (
        cfg["parties"] != {"lab": "lab", "auditor": "auditor"}
        or cfg.get("input_scope", {}).get("logger_key_id", "logger") != "logger"
    ):
        raise AuditError("unapproved_parties")
    if len({trust[k] for k in ("lab_key", "auditor_key", "logger_key")}) != 3:
        raise AuditError("invalid_trust_roots")
    if cfg["model"] != {k: trust["model"][k] for k in cfg["model"]}:
        raise AuditError("unapproved_model")
    if cfg["window"] > cfg["limits"]["max_logs_per_query"]:
        raise AuditError("invalid_limits")
    scope = cfg.get("input_scope")
    if scope and datetime.fromisoformat(scope["window_start"]) >= datetime.fromisoformat(
        scope["window_end"]
    ):
        raise AuditError("invalid_scope")
    prompt = f"prompts/{cfg['classification']['prompt_id']}.txt"
    if prompt not in files or not files[prompt].strip():
        raise AuditError("missing_prompt")
    check_source(files["plan.py"].decode())
    check_schema(cfg["output_schema"])
    commitment = file_digest(files)
    if signed:
        for role in ("lab", "auditor"):
            path = folder / "signatures" / f"{role}.sig"
            if path.is_symlink() or path.stat().st_size > 256:
                raise AuditError("invalid_signature")
            verify_signature(
                trust[role + "_key"],
                path.read_text().strip(),
                "plan-bundle",
                {"plan_digest": commitment},
            )
    return Bundle(files, commitment, cfg)


def restrictions(trust):
    return {
        "version": "0.1.0",
        "plan_imports": IMPORTS,
        "hardware": {"tee": "Tinfoil confidential VM", "gpu": "separately attested inference"},
        "models": [trust["model"]],
        "limits": LIMITS,
        "input_serialization_version": "aiab-records/v1",
        "output_schema_subset": "bounded-json/v1",
        "sandbox": "linux-x86_64-seccomp-worker/v1",
    }
