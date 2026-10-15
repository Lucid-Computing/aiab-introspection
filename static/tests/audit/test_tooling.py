"""Tamper resistance and semantic tests for operator tooling."""

import importlib.util
import json
from pathlib import Path

import pytest

from aiab.audit.protocol import AuditError, clone, digest
from auditor.evaluate import decision_digest, summarize, validate_dataset
from auditor.offline import verify_bundle
from auditor.schema_info import capacity

STATIC = Path(__file__).resolve().parents[2]


def bundle():
    evidence = json.loads((STATIC / "demo/audit/evidence/acceptance.json").read_text())
    manifest = json.loads((STATIC / "demo/audit/evidence/manifest.json").read_text())
    anchor = {
        "schema": "aiab-identity-anchor/v1",
        "identity": evidence["identity"],
        "policy_digest": evidence["policy_digest"],
    }
    data = {
        "schema": "aiab-offline-bundle/v1",
        "manifest": manifest,
        "identity": evidence["identity"],
        "signed_plan": evidence["signed_plan"],
        "envelope": evidence["reports"][0]["envelope"],
    }
    report = data["envelope"]["report"]
    expected = [report["run_id"], report["input_commitment"], report["plan_digest"]]
    return data, anchor, expected


def test_offline_saved_signature_and_input_without_network(monkeypatch):
    import socket

    monkeypatch.setattr(socket, "socket", lambda *a, **kw: pytest.fail("network forbidden"))
    data, anchor, expected = bundle()
    result = verify_bundle(data, anchor, *expected)
    assert result["verified"]
    assert result["egress_max_bits"] == 1
    assert result["report"]["result"] == {"flagged": False}


@pytest.mark.parametrize(
    "attack", ["identity", "manifest", "plan", "result", "run", "input", "expected-plan"]
)
def test_offline_tampering_rejected(attack):
    data, anchor, expected = bundle()
    data = clone(data)
    if attack == "identity":
        data["identity"]["public_key"] = "A" * 43 + "="
    elif attack == "manifest":
        data["manifest"]["max_active_runs"] = 999
    elif attack == "plan":
        data["signed_plan"]["plan"]["classification"]["threshold"] = 0.1
    elif attack == "result":
        data["envelope"]["report"]["result"]["flagged"] = True
    else:
        expected[{"run": 0, "input": 1, "expected-plan": 2}[attack]] = "wrong"
    with pytest.raises(AuditError):
        verify_bundle(data, anchor, *expected)


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


def test_evaluation_config_commitment_excludes_only_record_and_name():
    data, _, _ = bundle()
    plan = data["signed_plan"]["plan"]
    altered = clone(plan)
    altered["plan_id"] = "new"
    altered["classification"]["evaluation"]["results_digest"] = "sha256:" + "0" * 64
    assert decision_digest(altered) == decision_digest(plan)
    altered["classification"]["threshold"] = 0.9
    assert decision_digest(altered) != decision_digest(plan)


def test_evaluation_errors_not_counted_as_negative():
    result = summarize([{"outcome": x} for x in ("tp", "fn", "tn", "errors")])
    assert result["fnr"] == {"numerator": 1, "denominator": 2}
    assert result["fpr"] == {"numerator": 0, "denominator": 1}
    assert result["error_rate"] == {"numerator": 1, "denominator": 4}
    assert summarize([])["fnr"]["denominator"] == 0


def test_invalid_dataset_rejected_before_any_request():
    data, _, _ = bundle()
    plan = data["signed_plan"]["plan"]
    dataset = [{"id": "a", "text": "private", "expected": {"flagged": False}}]
    validate_dataset(dataset, plan, {"flagged": True}, {"flagged": False})
    dataset.append(clone(dataset[0]))
    with pytest.raises(AuditError):
        validate_dataset(dataset, plan, {"flagged": True}, {"flagged": False})


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
