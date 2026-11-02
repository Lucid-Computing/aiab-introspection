"""Strict JSON, RFC 8785 signatures and closed JSON Schema contracts."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jsonschema import Draft202012Validator, FormatChecker


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def strict_json_loads(value: str | bytes) -> Any:
    def reject_constant(_: str) -> None:
        raise ValueError("Non-finite JSON number")

    return json.loads(value, object_pairs_hook=_unique_object, parse_constant=reject_constant)


class AuditError(ValueError):
    def __init__(self, code: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(code)


def canonical(value: Any) -> bytes:
    try:
        return rfc8785.dumps(value)
    except (ValueError, TypeError, OverflowError):
        raise AuditError("invalid_json") from None


def decode(raw: bytes | str) -> Any:
    try:
        value = strict_json_loads(raw)
        canonical(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise AuditError("invalid_json") from None


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def sign_bytes(kind: str, value: Any) -> bytes:
    return ("AIAB/" + kind + "/v1\x00").encode() + canonical(value)


def verify_signature(key: str, signature: str, kind: str, value: Any) -> None:
    try:
        public = base64.b64decode(key, validate=True)
        signed = base64.b64decode(signature, validate=True)
        Ed25519PublicKey.from_public_bytes(public).verify(signed, sign_bytes(kind, value))
    except (ValueError, TypeError, InvalidSignature):
        raise AuditError("invalid_signature") from None


def closed(properties: dict, required: list | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


DIGEST = {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}
TEXT = {"type": "string", "minLength": 1, "maxLength": 16384}
ID = {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,128}$"}
KEY = {"type": "string", "pattern": "^[A-Za-z0-9+/]{43}=$"}
DATE = {"type": "string", "format": "date-time", "maxLength": 32}
SCOPE = closed(
    {"logger_key": KEY, "window_start": DATE, "window_end": DATE, "selection": {"const": "all"}}
)
MODEL = closed(
    {
        "id": TEXT,
        "artifact_digest": DIGEST,
        "repo": TEXT,
        "release_digest": DIGEST,
        "host": {"type": "string", "pattern": "^[a-z0-9.-]+\\.tinfoil\\.dev$"},
        "temperature": {"const": 0},
        "max_output_tokens": {"type": "integer", "minimum": 32, "maximum": 2048},
        "context_tokens": {"type": "integer", "minimum": 1024, "maximum": 131072},
    }
)
PLAN = closed(
    {
        "schema": {"const": "aiab-plan/v1"},
        "plan_id": ID,
        "parties": closed({"lab_key": KEY, "auditor_key": KEY}),
        "runtime_digest": DIGEST,
        "input_scope": SCOPE,
        "model": MODEL,
        "classification": closed(
            {
                "problem": TEXT,
                "prompt": TEXT,
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
                "threshold": {"type": "number", "minimum": 0, "maximum": 1},
                "evaluation": closed({"test_set_digest": DIGEST, "results_digest": DIGEST}),
            }
        ),
        "program": {"type": "string", "minLength": 1, "maxLength": 16384},
        "output_schema": {"type": "object"},
        "limits": closed(
            {
                "max_input_utf8_bytes": {"type": "integer", "minimum": 1, "maximum": 1000000},
                "max_model_calls": {"const": 1},
                "execution_timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 300},
                "max_result_bytes": {"type": "integer", "minimum": 1, "maximum": 4096},
            }
        ),
    }
)
SIGNED_PLAN = closed({"plan": PLAN, "signatures": closed({"lab": TEXT, "auditor": TEXT})})
SUBMISSION = closed(
    {
        "submission_id": ID,
        "plan_digest": DIGEST,
        "text": {"type": "string", "maxLength": 1000000},
        "logger_evidence": {"type": "object"},
    }
)


def validate(value: Any, schema: dict, code: str = "invalid_plan") -> None:
    try:
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        if next(validator.iter_errors(value), None) is not None:
            raise AuditError(code)
    except (RecursionError, TypeError, ValueError):
        raise AuditError(code) from None


def check_output_schema(schema: Any, depth: int = 0) -> None:
    """Small finite output language; no remote refs, regexes or open containers."""
    if not isinstance(schema, dict) or depth > 5 or len(canonical(schema)) > 16384:
        raise AuditError("invalid_output_schema")
    kind = schema.get("type")
    allowed = {"type", "$schema"} if depth == 0 else {"type"}
    if "$schema" in schema and schema["$schema"] != "https://json-schema.org/draft/2020-12/schema":
        raise AuditError("invalid_output_schema")
    if kind == "boolean":
        pass
    elif kind == "integer":
        allowed |= {"minimum", "maximum"}
        if not all(type(schema.get(k)) is int for k in ("minimum", "maximum")):
            raise AuditError("invalid_output_schema")
        if not -(2**31) <= schema["minimum"] <= schema["maximum"] <= 2**31:
            raise AuditError("invalid_output_schema")
    elif kind == "string":
        allowed |= {"enum"}
        values = schema.get("enum")
        if (
            not isinstance(values, list)
            or not 1 <= len(values) <= 32
            or any(not isinstance(v, str) or len(v) > 128 for v in values)
        ):
            raise AuditError("invalid_output_schema")
    elif kind == "object":
        allowed |= {"properties", "required", "additionalProperties"}
        props = schema.get("properties")
        if (
            not isinstance(props, dict)
            or not 1 <= len(props) <= 16
            or schema.get("additionalProperties") is not False
            or not isinstance(schema.get("required"), list)
            or sorted(schema["required"]) != sorted(props)
        ):
            raise AuditError("invalid_output_schema")
        for name, child in props.items():
            if not isinstance(name, str) or not 1 <= len(name) <= 64:
                raise AuditError("invalid_output_schema")
            check_output_schema(child, depth + 1)
    else:
        raise AuditError("invalid_output_schema")
    if set(schema) - allowed:
        raise AuditError("invalid_output_schema")
    try:
        Draft202012Validator.check_schema(schema)
    except Exception:
        raise AuditError("invalid_output_schema") from None


def clone(value: Any) -> Any:
    return json.loads(canonical(value))
