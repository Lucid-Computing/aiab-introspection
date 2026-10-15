"""Conservative result-payload capacity, derived from the signed finite schema."""

import math

from aiab.audit.protocol import check_output_schema


def capacity(schema, max_result_bytes):
    check_output_schema(schema)

    def count(node):
        match node["type"]:
            case "boolean":
                return 2
            case "integer":
                return node["maximum"] - node["minimum"] + 1
            case "string":
                return len(set(node["enum"]))
            case "object":
                return math.prod(count(child) for child in node["properties"].values())
        raise ValueError("unsupported schema")

    possible = count(schema)
    bits = (possible - 1).bit_length()
    return {
        "egress_max_bits": min(bits, 8 * max_result_bytes),
        "scope": "result-payload-only",
        "bound": "conservative",
        "excludes": ["metadata", "errors", "timing", "repeated-runs"],
    }
