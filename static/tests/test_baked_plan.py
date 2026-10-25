"""Baked bundle admission, isolated execution and signed-report failure semantics."""

import asyncio
import base64
import json
import shutil
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aiab.audit.api import create_app
from aiab.audit.protocol import AuditError, canonical, clone, decode, sign_bytes
from aiab.baked.bundle import load_bundle
from aiab.baked.executor import execute
from aiab.baked.output import capacity, check_schema
from aiab.baked.records import export_statement, scope_of, verify
from aiab.baked.runtime import Runtime, verify_report
from aiab.dryrun import MockModel

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def setup(tmp_path):
    folder = tmp_path / "plan"
    fixture = Path(__file__).resolve().parent / "fixtures" / "demo-plan"  # frozen demo plan
    shutil.copytree(fixture, folder)
    (folder / "trust.json").unlink()  # the fixture's trust file is not part of a plan folder
    trust = decode((fixture / "trust.json").read_bytes())
    keys = {role: Ed25519PrivateKey.generate() for role in ("lab", "auditor", "logger")}
    for role, key in keys.items():
        trust[role + "_key"] = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    trust["runtime_digest"] = "sha256:" + "a" * 64
    unsigned = load_bundle(folder, trust, signed=False)
    for role in ("lab", "auditor"):
        (folder / "signatures" / f"{role}.sig").write_text(
            base64.b64encode(
                keys[role].sign(sign_bytes("plan-bundle", {"plan_digest": unsigned.digest}))
            ).decode()
        )
    bundle = load_bundle(folder, trust)
    records = [
        decode(line)
        for line in (folder / "tests/transcripts/benign_coding.jsonl").read_bytes().splitlines()
    ]
    return folder, trust, keys, bundle, records


def submission(setup, records=None):
    _, _, keys, bundle, default = setup
    records = default if records is None else records
    scope = bundle.config["input_scope"]
    statement = export_statement(records, scope)
    return {
        "submission_id": "case-1",
        "plan_digest": bundle.digest,
        "input_scope": scope,
        "records": records,
        "logger_evidence": {
            "statement": statement,
            "signature": base64.b64encode(
                keys["logger"].sign(sign_bytes("record-export", statement))
            ).decode(),
        },
    }


@pytest.mark.parametrize("file", ["plan.py", "plan.json", "prompts/bio_research_v1.txt"])
def test_every_executable_byte_requires_new_signatures(setup, file):
    folder, trust, _, bundle, _ = setup
    path = folder / file
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(AuditError, match="invalid_signature"):
        load_bundle(folder, trust)
    assert load_bundle(folder, trust, signed=False).digest != bundle.digest


def test_fixture_edits_do_not_change_executable_commitment(setup):
    folder, trust, _, bundle, _ = setup
    (folder / "tests/expected.json").write_text("{}")
    assert load_bundle(folder, trust).digest == bundle.digest


@pytest.mark.parametrize("attack", ["text", "agent", "timestamp", "scope", "order"])
def test_record_proof_rejects_tampering_before_inference(setup, attack):
    _, trust, _, bundle, _ = setup
    data = clone(submission(setup))
    if attack == "text":
        data["records"][0]["content"] = "tampered"
    if attack == "agent":
        data["records"][0]["agent_id"] = "outsider"
    if attack == "timestamp":
        data["records"][0]["ts"] = "2020-01-01T00:00:00Z"
    if attack == "scope":
        data["input_scope"]["agents"] = ["outsider"]
    if attack == "order":
        data["records"].reverse()
    with pytest.raises(AuditError, match="invalid_logger_evidence"):
        verify(data, bundle.config, trust["logger_key"])


def test_authentic_but_out_of_scope_rejected(setup):
    _, trust, _, bundle, records = setup
    records = clone(records)
    records[0]["agent_id"] = "outside"
    with pytest.raises(AuditError, match="scope_mismatch"):
        verify(submission(setup, records), bundle.config, trust["logger_key"])


async def test_real_worker_with_broker_and_api_report(setup):
    _, trust, _, bundle, _ = setup
    runtime = Runtime(bundle, trust, MockModel())
    tokens = {role: role + "-" + "x" * 40 for role in ("lab", "auditor", "logger")}
    app = create_app(runtime, tokens)
    data = submission(setup)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"authorization": "Bearer " + tokens["lab"]},
    ) as client:
        assert (await client.post("/aiab/plans", json={})).status_code == 405
        response = await client.post("/aiab/runs", json=data)
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        await runtime.runs[run_id].task
        envelope = (await client.get(f"/aiab/runs/{run_id}/report")).json()
        commitment = verify(data, bundle.config, trust["logger_key"])
        result = verify_report(
            envelope,
            runtime.identity,
            bundle,
            runtime.manifest,
            run_id=run_id,
            input_commitment=commitment,
        )
        assert result["result"] == {"flagged": False}
        assert result["egress_max_bits"] == 1
        assert (await client.post("/aiab/runs", json=data)).json()["run_id"] == run_id
        bad = clone(envelope)
        bad["report"]["image_digest"] = "sha256:" + "0" * 64
        with pytest.raises(AuditError):
            verify_report(
                bad,
                runtime.identity,
                bundle,
                runtime.manifest,
                run_id=run_id,
                input_commitment=commitment,
            )
    await runtime.close()


async def test_context_preflight_all_queries_before_any_inference(setup):
    _, _, _, bundle, records = setup
    cfg = clone(bundle.config)
    cfg["window"] = 1
    bundle = replace(bundle, config=cfg)

    class Overflow(MockModel):
        checked = 0
        calls = 0

        async def check_context(self, text, policy):
            self.checked += 1
            if self.checked == 2:
                raise AuditError("input_too_large")

        async def classify(self, *args):
            self.calls += 1
            return 0.1

    model = Overflow()
    with pytest.raises(AuditError, match="input_too_large"):
        await execute(bundle, records, model)
    assert model.checked == 2 and model.calls == 0


@pytest.mark.parametrize(
    "operation",
    [
        "open('/etc/passwd').read()",
        "__import__('socket').socket()",
        "__import__('subprocess').run(['true'])",
    ],
)
async def test_os_boundary_even_if_source_checker_is_bypassed(setup, operation):
    _, _, _, bundle, records = setup
    files = dict(bundle.files)
    files["plan.py"] = files["plan.py"].replace(
        b"    kept: list[LogRecord] = []",
        ("    " + operation + "\n    kept: list[LogRecord] = []").encode(),
    )
    # Deliberately bypass static checks to test the actual syscall boundary.
    with pytest.raises(AuditError, match="execution_failed"):
        await execute(replace(bundle, files=files), records, MockModel())


async def test_timeout_kills_worker_and_signs_failure(setup):
    _, trust, _, bundle, _ = setup
    files = dict(bundle.files)
    files["plan.py"] = files["plan.py"].replace(
        b"    kept: list[LogRecord] = []", b"    while True: pass\n    kept: list[LogRecord] = []"
    )
    cfg = clone(bundle.config)
    cfg["limits"]["max_runtime_seconds_per_run"] = 1
    runtime = Runtime(replace(bundle, files=files, config=cfg), trust, MockModel())
    run = runtime.submit(submission(setup), "lab")
    await asyncio.wait_for(run.task, 4)
    envelope = decode(run.envelope)
    assert envelope["report"]["error"] == "execution_failed"
    await runtime.close()


async def test_invalid_output_never_released(setup):
    _, trust, _, bundle, _ = setup
    files = dict(bundle.files)
    files["plan.py"] = files["plan.py"].replace(
        b'return {"flagged": verdict.flagged}', b'return {"raw": "private"}'
    )
    runtime = Runtime(replace(bundle, files=files), trust, MockModel())
    run = runtime.submit(submission(setup), "lab")
    await run.task
    assert decode(run.envelope)["report"]["error"] == "invalid_output"
    assert b"private" not in run.envelope
    await runtime.close()


def test_bounded_strings_and_arrays_have_finite_schema_capacity():
    schema = {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 4}}
    assert check_schema(schema) > 0
    assert capacity({"output_schema": schema, "limits": {"max_result_bytes": 4}}) == 32
    with pytest.raises(AuditError):
        check_schema({"type": "array", "items": {"type": "boolean"}})


def test_ci_plan_check_uses_base_keys_and_rejects_mixed_changes(setup, tmp_path, monkeypatch):
    import subprocess

    from deploy.check_plan_pr import check

    folder, trust, keys, _, _ = setup
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copytree(folder, repo / "plan")
    fixed = repo / "static/aiab/baked/trust.json"
    fixed.parent.mkdir(parents=True)
    fixed.write_bytes(canonical(trust))
    monkeypatch.chdir(repo)

    def git(*args):
        return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL).decode().strip()

    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    prompt = repo / "plan/prompts/bio_research_v1.txt"
    prompt.write_text(prompt.read_text() + "\nReviewed change.\n")
    updated = load_bundle(repo / "plan", trust, signed=False)
    for role in ("lab", "auditor"):
        (repo / "plan/signatures" / f"{role}.sig").write_text(
            base64.b64encode(
                keys[role].sign(sign_bytes("plan-bundle", {"plan_digest": updated.digest}))
            ).decode()
        )
    git("add", "plan")
    git("commit", "-qm", "signed plan")
    assert check(base, git("rev-parse", "HEAD")) == updated.digest
    (repo / "plan/plan.py").write_text((repo / "plan/plan.py").read_text() + "\n")
    git("add", "plan")
    git("commit", "-qm", "tamper")
    with pytest.raises(AuditError, match="invalid_signature"):
        check(base, git("rev-parse", "HEAD"))
    fixed.write_bytes(canonical({**trust, "lab_key": trust["auditor_key"]}))
    git("add", "static")
    git("commit", "-qm", "change verifier trust")
    with pytest.raises(ValueError, match="plan-only"):
        check(base, git("rev-parse", "HEAD"))


@pytest.mark.parametrize(
    "prefix",
    [
        "import dataclasses\n",
        "from typing import sys\n",
        "from dataclasses import inspect\n",
        "from aiab.plan_api import install_run_llm\n",
    ],
)
def test_only_named_plan_api_imports_are_admitted(setup, prefix):
    from aiab.baked.bundle import check_source

    source = setup[3].files["plan.py"].decode()
    # Place after the future import, so rejection is not a syntax error.
    source = source.replace("from typing import Literal", prefix + "from typing import Literal")
    with pytest.raises(AuditError, match="invalid_plan_source"):
        check_source(source)


def test_plan_without_input_scope_accepts_any_agent(setup):
    folder, trust, keys, _, records = setup
    cfg_path = folder / "plan.json"
    cfg = json.loads(cfg_path.read_text())
    cfg.pop("input_scope")
    cfg_path.write_text(json.dumps(cfg))
    bundle = load_bundle(folder, trust, signed=False)
    outsiders = [dict(r, agent_id="outsider") for r in records]
    scope = scope_of(outsiders)
    statement = export_statement(outsiders, scope)
    data = {
        "submission_id": "case-2",
        "plan_digest": bundle.digest,
        "input_scope": scope,
        "records": outsiders,
        "logger_evidence": {
            "statement": statement,
            "signature": base64.b64encode(
                keys["logger"].sign(sign_bytes("record-export", statement))
            ).decode(),
        },
    }
    verify(data, bundle.config, trust["logger_key"])  # no AuditError
    # the same records are still refused by a plan that pins other agents
    with pytest.raises(AuditError, match="scope_mismatch"):
        verify(data, setup[3].config, trust["logger_key"])
