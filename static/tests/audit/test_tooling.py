"""Semantic tests for operator tooling."""

import importlib.util
import json
from pathlib import Path

import pytest

from aiab.audit.protocol import digest
from auditor.evaluate import summarize
from auditor.schema_info import capacity

STATIC = Path(__file__).resolve().parents[2]


def test_schema_capacity_counts_product_before_rounding():
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "string", "enum": ["a", "b", "c"]},
            "b": {"type": "string", "enum": ["a", "b", "c"]},
        },
        "required": ["a", "b"],
        "additionalProperties": False,
    }
    assert capacity(schema, 1024)["egress_max_bits"] == 4  # ceil(log2(9))
    assert capacity({"type": "integer", "minimum": 2, "maximum": 2}, 1024)["egress_max_bits"] == 0
    assert capacity({"type": "string", "enum": ["a", "a", "b"]}, 1024)["egress_max_bits"] == 1


def test_evaluation_errors_not_counted_as_negative():
    result = summarize([{"outcome": x} for x in ("tp", "fn", "tn", "errors")])
    assert result["fnr"] == {"numerator": 1, "denominator": 2}
    assert result["fpr"] == {"numerator": 0, "denominator": 1}
    assert result["error_rate"] == {"numerator": 1, "denominator": 4}
    assert summarize([])["fnr"]["denominator"] == 0


def test_release_render_preserves_secret_names_and_requires_reproducibility():
    spec = importlib.util.spec_from_file_location(
        "release", STATIC / "deploy/tinfoil/audit/release.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    folder = STATIC / "deploy/tinfoil/audit"
    config = json.loads((folder / "tinfoil-config.yml").read_text())
    trust = json.loads((folder / "trust.json").read_text())
    build = {
        "schema": "aiab-build/v1",
        "image": "ghcr.io/org/audit",
        "digest": "sha256:" + "a" * 64,
        "reproduced_digest": "sha256:" + "a" * 64,
    }
    new_config, new_trust, manifest = module.render(config, trust, build, "v0.1.3")
    assert new_config["containers"][0]["secrets"] == config["containers"][0]["secrets"]
    assert new_trust["runtime_digest"] == build["digest"]
    assert {"AIAB_RELEASE_TAG": "v0.1.3"} in new_config["containers"][0]["env"]
    assert digest(manifest) != digest(json.loads((folder / "manifest.json").read_text()))
    build["reproduced_digest"] = "sha256:" + "b" * 64
    with pytest.raises(ValueError):
        module.render(config, trust, build, "v0.1.3")
