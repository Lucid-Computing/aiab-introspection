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
from aiab.baked.plan_ir import check_plan
from aiab.baked.records import export_statement, scope_of, verify
from aiab.baked.runtime import Runtime, verify_report
from aiab.dryrun import MockModel

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def setup(tmp_path):
    folder = tmp_path / "plan"
    fixture = Path(__file__).resolve().parent / "fixtures" / "graph-plan"  # frozen demo plan
    shutil.copytree(fixture, folder)
    (folder / "trust.json").unlink()  # the fixture's trust file is not part of a plan folder
    trust = decode((fixture / "trust.json").read_bytes())
    keys = {role: Ed25519PrivateKey.generate() for role in ("lab", "auditor", "logger")}
    for role, key in keys.items():
        trust[role + "_key"] = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    trust["runtime_digest"] = "sha256:" + "a" * 64
    unsigned = load_bundle(folder, trust, signed=False)
    (folder / "signatures").mkdir(exist_ok=True)  # the fixture ships demo signatures
    for role in ("lab", "auditor"):
        (folder / "signatures" / f"{role}.sig").write_text(
            base64.b64encode(
                keys[role].sign(sign_bytes("plan-bundle", {"plan_digest": unsigned.digest}))
            ).decode()
        )
    bundle = load_bundle(folder, trust)
    records = [
        decode(line)
        for line in (folder / "tests/transcripts/benign_feature.jsonl").read_bytes().splitlines()
    ]
    return folder, trust, keys, bundle, records


def submission(setup, records=None, submission_id="case-1"):
    _, _, keys, bundle, default = setup
    records = default if records is None else records
    scope = bundle.config.get("input_scope") or scope_of(records)
    statement = export_statement(records, scope)
    return {
        "submission_id": submission_id,
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


def edited(bundle, change, check=True):
    """The bundle with its plan changed in memory. With ``check`` the loader's analysis is
    redone (order, limits, schema); without it the change bypasses the loader on purpose."""
    plan = clone(bundle.plan)
    change(plan)
    if not check:
        return replace(bundle, plan=plan)
    analysis = check_plan(plan)
    return replace(bundle, plan=plan, order=analysis.order, config=analysis.config)


def node(plan, node_id):
    return next(n for n in plan["nodes"] if n["id"] == node_id)


def test_every_executable_byte_requires_new_signatures(setup):
    folder, trust, _, bundle, _ = setup
    path = folder / "plan.json"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(AuditError, match="invalid_signature"):
        load_bundle(folder, trust)
    assert load_bundle(folder, trust, signed=False).digest != bundle.digest


def test_fixture_edits_do_not_change_executable_commitment(setup):
    folder, trust, _, bundle, _ = setup
    (folder / "tests/expected.json").write_text("{}")
    (folder / "README.md").write_text("notes")
    assert load_bundle(folder, trust).digest == bundle.digest


def test_loader_reports_why_a_plan_is_refused(setup):
    folder, trust, _, _, _ = setup
    plan = json.loads((folder / "plan.json").read_text())
    node(plan, "report")["inputs"]["result"] = "logs.records"
    (folder / "plan.json").write_bytes(canonical(plan))
    with pytest.raises(AuditError, match="invalid_plan") as failure:
        load_bundle(folder, trust, signed=False)
    assert failure.value.issues[0]["node"] == "report"
    assert "receives LogRecords" in failure.value.issues[0]["message"]


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


def test_plan_pinning_agents_refuses_other_agents(setup):
    folder, trust, _, bundle, records = setup
    plan = json.loads((folder / "plan.json").read_text())
    node(plan, "logs")["config"]["input_scope"] = {
        "window_start": "2026-10-01T00:00:00Z",
        "window_end": "2026-11-01T00:00:00Z",
        "agents": ["dev-agent-9"],
    }
    (folder / "plan.json").write_bytes(canonical(plan))
    pinned = load_bundle(folder, trust, signed=False)
    assert pinned.config["input_scope"]["agents"] == ["dev-agent-9"]
    data = submission(setup)
    with pytest.raises(AuditError, match="scope_mismatch"):
        verify(data, pinned.config, trust["logger_key"])
    verify(data, bundle.config, trust["logger_key"])  # the fixture plan takes any agent


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
        assert result["result"] == {"flagged": False, "destructive_sessions": 0, "sessions": 1}
        assert result["egress_max_bits"] == 67
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


async def test_destructive_session_is_flagged_through_the_worker(setup):
    folder, _, _, bundle, _ = setup
    records = [
        decode(line)
        for line in (folder / "tests/transcripts/mixed_sessions.jsonl").read_bytes().splitlines()
    ]
    result = await execute(bundle, records, MockModel())
    assert result == {"flagged": True, "destructive_sessions": 1, "sessions": 2}


async def test_broker_builds_prompts_and_preflights_each_node_before_inference(setup):
    _, _, _, bundle, records = setup

    class Spy(MockModel):
        events = []

        async def check_messages(self, content, max_output_tokens):
            assert content[0]["role"] == "system" and content[1]["role"] == "user"
            assert "untrusted evidence" in content[0]["content"]  # the box's framing, first
            self.events.append(("check", max_output_tokens))

        async def complete(self, content, schema, max_output_tokens):
            self.events.append(("complete", max_output_tokens))
            return await super().complete(content, schema, max_output_tokens)

    model = Spy()
    await execute(bundle, records, model)
    # effects: one session, 400 tokens; destructive: one item, 256 tokens. Checks first.
    assert model.events == [
        ("check", 400),
        ("complete", 400),
        ("check", 256),
        ("complete", 256),
    ]


async def test_context_preflight_all_items_before_any_inference(setup):
    _, _, _, bundle, records = setup

    # Classify every record on its own: several items in the one classifier batch.
    def direct(p):
        node(p, "destructive")["inputs"]["items"] = "logs.records"
        node(p, "destructive")["config"]["group_by"] = "record"
        p["nodes"] = [n for n in p["nodes"] if n["id"] not in ("commands", "effects")]

    bundle = edited(bundle, direct)

    class Overflow(MockModel):
        checked = 0
        calls = 0

        async def check_messages(self, content, max_output_tokens):
            self.checked += 1
            if self.checked == 2:
                raise AuditError("input_too_large")

        async def complete(self, *args):
            self.calls += 1
            return {"label": "safe", "score": 0.9}

    model = Overflow()
    with pytest.raises(AuditError, match="input_too_large"):
        await execute(bundle, records, model)
    assert model.checked == 2 and model.calls == 0


async def test_model_call_budget_is_enforced_by_the_parent(setup):
    _, _, _, bundle, records = setup
    limits = {**bundle.config["limits"], "max_model_calls_per_run": 1}
    bundle = replace(bundle, config={**bundle.config, "limits": limits})
    with pytest.raises(AuditError, match="input_too_large"):
        await execute(bundle, records, MockModel())


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
    # Deliberately bypass the static check (the loader ran already) to test the syscall boundary.
    bundle = edited(
        bundle,
        lambda p: node(p, "commands")["config"].__setitem__(
            "code", f"def run(records):\n    {operation}\n    return []\n"
        ),
        check=False,
    )
    with pytest.raises(AuditError, match="plan_metric_failed"):
        await execute(bundle, records, MockModel())


async def test_timeout_kills_worker_and_signs_failure(setup):
    _, trust, _, bundle, _ = setup
    bundle = edited(
        bundle,
        lambda p: node(p, "commands")["config"].__setitem__(
            "code", "def run(records):\n    while True: pass\n"
        ),
        check=False,
    )
    cfg = clone(bundle.config)
    cfg["limits"]["max_runtime_seconds_per_run"] = 1
    runtime = Runtime(replace(bundle, config=cfg), trust, MockModel())
    run = runtime.submit(submission(setup), "lab")
    await asyncio.wait_for(run.task, 4)
    envelope = decode(run.envelope)
    assert envelope["report"]["error"] == "execution_timeout"
    await runtime.close()


async def test_invalid_output_never_released(setup):
    _, trust, _, bundle, _ = setup
    # The broker checks the result against the egress schema independently of the worker.
    cfg = clone(bundle.config)
    cfg["output_schema"]["properties"].pop("sessions")
    cfg["output_schema"]["required"].remove("sessions")
    runtime = Runtime(replace(bundle, config=cfg), trust, MockModel())
    run = runtime.submit(submission(setup), "lab")
    await run.task
    assert decode(run.envelope)["report"]["error"] == "invalid_output"
    assert b"tool call" not in run.envelope and b"Bash" not in run.envelope
    await runtime.close()


async def test_worker_error_identifies_stage_without_private_exception_text(setup):
    _, _, _, bundle, records = setup
    bundle = edited(
        bundle,
        lambda p: node(p, "commands")["config"].__setitem__(
            "code", "def run(records):\n    raise ValueError('PRIVATE INPUT MUST NOT ESCAPE')\n"
        ),
        check=False,
    )
    with pytest.raises(AuditError) as failure:
        await execute(bundle, records, MockModel())
    assert failure.value.code == "plan_metric_failed"
    assert "PRIVATE" not in str(failure.value)


async def test_model_answers_are_validated_against_the_dictated_schema(setup):
    _, _, _, bundle, records = setup

    class Loose(MockModel):
        async def complete(self, content, schema, max_output_tokens):
            return {"label": "whatever", "score": 0.5}  # not one of the plan's labels

    with pytest.raises(AuditError, match="model_output_invalid"):
        await execute(bundle, records, Loose())


def test_bounded_strings_and_arrays_have_finite_schema_capacity():
    schema = {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 4}}
    assert check_schema(schema) > 0
    assert capacity({"output_schema": schema, "limits": {"max_result_bytes": 4}}) == 32
    assert check_schema({"type": "number"}) == 64
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
    plan_path = repo / "plan/plan.json"
    plan = json.loads(plan_path.read_text())
    node(plan, "destructive")["config"]["threshold"] = 0.7
    plan_path.write_bytes(canonical(plan) + b"\n")
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
    plan_path.write_bytes(plan_path.read_bytes() + b"\n")
    git("add", "plan")
    git("commit", "-qm", "tamper")
    with pytest.raises(AuditError, match="invalid_signature"):
        check(base, git("rev-parse", "HEAD"))
    fixed.write_bytes(canonical({**trust, "lab_key": trust["auditor_key"]}))
    git("add", "static")
    git("commit", "-qm", "change verifier trust")
    with pytest.raises(ValueError, match="plan-only"):
        check(base, git("rev-parse", "HEAD"))


def test_plan_without_input_scope_accepts_any_agent(setup):
    _, trust, keys, bundle, records = setup
    outsiders = [dict(r, agent_id="outsider") for r in records]
    data = submission(setup, outsiders)
    verify(data, bundle.config, trust["logger_key"])  # no AuditError
