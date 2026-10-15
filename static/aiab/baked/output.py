"""Bounded JSON outputs, including bounded strings/arrays, with finite bit bounds."""

from jsonschema import Draft202012Validator

from aiab.audit.protocol import AuditError, canonical, validate


def check_schema(node, depth=0):
    if not isinstance(node, dict) or depth > 5 or len(canonical(node)) > 16384:
        raise AuditError("invalid_output_schema")
    allowed = {"type"}
    if depth == 0 and "$schema" in node:
        allowed.add("$schema")
        if node["$schema"] != "https://json-schema.org/draft/2020-12/schema":
            raise AuditError("invalid_output_schema")
    kind = node.get("type")
    if kind == "boolean":
        bits = 1
    elif kind == "integer":
        allowed |= {"minimum", "maximum"}
        if (
            any(type(node.get(k)) is not int for k in ("minimum", "maximum"))
            or not -(2**31) <= node["minimum"] <= node["maximum"] <= 2**31
        ):
            raise AuditError("invalid_output_schema")
        bits = (node["maximum"] - node["minimum"]).bit_length()
    elif kind == "string":
        if "enum" in node:
            allowed.add("enum")
            values = node["enum"]
            if (
                not isinstance(values, list)
                or not 1 <= len(values) <= 32
                or any(not isinstance(s, str) or len(s) > 128 for s in values)
            ):
                raise AuditError("invalid_output_schema")
            bits = (len(set(values)) - 1).bit_length()
        else:
            allowed |= {"minLength", "maxLength"}
            if type(node.get("maxLength")) is not int or not 0 <= node["maxLength"] <= 1024:
                raise AuditError("invalid_output_schema")
            bits = 21 * node["maxLength"] + (node["maxLength"] + 1).bit_length()
    elif kind == "array":
        allowed |= {"items", "minItems", "maxItems"}
        if type(node.get("maxItems")) is not int or not 0 <= node["maxItems"] <= 64:
            raise AuditError("invalid_output_schema")
        bits = (
            node["maxItems"] * check_schema(node.get("items"), depth + 1)
            + (node["maxItems"] + 1).bit_length()
        )
    elif kind == "object":
        allowed |= {"properties", "required", "additionalProperties"}
        props = node.get("properties")
        if (
            not isinstance(props, dict)
            or not 1 <= len(props) <= 16
            or node.get("additionalProperties") is not False
            or not isinstance(node.get("required"), list)
            or sorted(node["required"]) != sorted(props)
        ):
            raise AuditError("invalid_output_schema")
        if any(not 1 <= len(k) <= 64 for k in props):
            raise AuditError("invalid_output_schema")
        bits = sum(check_schema(v, depth + 1) for v in props.values())
    else:
        raise AuditError("invalid_output_schema")
    if set(node) - allowed:
        raise AuditError("invalid_output_schema")
    try:
        Draft202012Validator.check_schema(node)
    except Exception:
        raise AuditError("invalid_output_schema") from None
    return bits


def check_result(result, cfg):
    validate(result, cfg["output_schema"], "invalid_output")
    if len(canonical(result)) > cfg["limits"]["max_result_bytes"]:
        raise AuditError("invalid_output")


def capacity(cfg):
    return min(check_schema(cfg["output_schema"]), cfg["limits"]["max_result_bytes"] * 8)
