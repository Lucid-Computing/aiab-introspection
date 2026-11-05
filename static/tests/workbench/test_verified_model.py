"""Explicit protocol doubles; these checks never count as live attestation/inference."""

import asyncio
import copy

import pytest

from aiab.workbench import model as module
from aiab.workbench.model import (
    MODEL_PROFILE,
    REVIEWED_HOST,
    REVIEWED_TLS_KEY,
    DeepSeekModel,
    GoPinnedConnection,
)
from aiab.workbench.tools import RuntimeFailure


def document():
    return {
        "securityVerified": True,
        "freshnessExpiresAt": "2099-01-01T00:00:00+00:00",
        "configRepo": MODEL_PROFILE["repo"],
        "enclaveHost": REVIEWED_HOST,
        "releaseDigest": MODEL_PROFILE["release_digest"].removeprefix("sha256:"),
        "tlsPublicKey": REVIEWED_TLS_KEY,
        "verifier": {"name": "tinfoil-go", "version": "0.16.4"},
        "steps": {
            s: {"status": "success"}
            for s in ("fetchDigest", "verifyCode", "verifyEnclave", "compareMeasurements")
        },
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("freshnessExpiresAt", "2000-01-01T00:00:00+00:00"),
        ("configRepo", "tinfoilsh/other"),
        ("releaseDigest", "new-release"),
        ("tlsPublicKey", "new-key"),
        ("securityVerified", False),
        ("enclaveHost", "other.tinfoil.dev"),
        ("steps", {}),
        ("verifier", {"name": "tinfoil-go", "version": "unreviewed"}),
    ],
)
def test_document_requires_all_pins(field, value):
    c = GoPinnedConnection(REVIEWED_HOST, "test-only")
    doc = document()
    c.check_document(doc)
    doc[field] = value
    with pytest.raises(RuntimeFailure, match="verified_transport_failed"):
        c.check_document(doc)


@pytest.fixture
def stub(tmp_path, monkeypatch):
    def create(action="normal"):
        path = tmp_path / "broker"
        script = f"""#!/usr/bin/python3
import json,sys,time,os
f=json.loads(sys.stdin.readline())
assert os.environ.get('TINFOIL_API_KEY') is None
assert len(sys.argv)==1
print(json.dumps({{"ok":True,"verification":{document()!r}}}),flush=True)
f=json.loads(sys.stdin.readline())
assert 'credential' not in f
if {action!r} == 'sleep': time.sleep(30)
elif {action!r} == 'malformed': print('data: broken',flush=True)
elif {action!r} == 'oversize': print('x'*2097153,flush=True)
elif {action!r} == 'eof': sys.exit(0)
elif {action!r} == 'key':
 d={document()!r};d['tlsPublicKey']='rotated'
 print(json.dumps({{"ok":True,"status":200,"body":{{"count":10}},"verification":d}}),flush=True)
else:
 print(json.dumps({{"ok":True,"status":200,"body":{{"count":10}},"verification":{document()!r}}}),flush=True)
"""
        path.write_text(script)
        path.chmod(0o700)
        monkeypatch.setattr(module, "BROKER", path)

    return create


async def test_pipe_credential_and_normal_count(stub, monkeypatch):
    stub()
    monkeypatch.setenv("TINFOIL_API_KEY", "must-not-inherit")
    m = DeepSeekModel(host=REVIEWED_HOST, credential="test-only", transport="go")
    try:
        assert m.credential == ""
        await m.start()
        assert m.connection.credential == ""
        assert await m.count_tokens([], []) == 10
        assert m.verification_document()["tlsPublicKey"] == REVIEWED_TLS_KEY
    finally:
        await m.close()
    assert m.connection.process.returncode is not None


@pytest.mark.parametrize("action", ["malformed", "oversize", "eof", "key", "sleep"])
async def test_protocol_failures_poison_and_reap(stub, action):
    stub(action)
    c = GoPinnedConnection(REVIEWED_HOST, "test-only")
    await c.start()
    with pytest.raises(RuntimeFailure, match="verified_transport_failed"):
        await c.request("POST", "/tokenize", b'{"model":"deepseek-v4-1-flash"}', timeout=0.1)
    assert c.failed and c.process.returncode is not None
    with pytest.raises(RuntimeFailure):
        await c.request("POST", "/tokenize", b"{}")


async def test_cancellation_poison_and_reap(stub):
    stub("sleep")
    c = GoPinnedConnection(REVIEWED_HOST, "test-only")
    await c.start()
    task = asyncio.create_task(c.request("POST", "/tokenize", b"{}"))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert c.failed and c.process.returncode is not None


@pytest.mark.parametrize("tokens", [0, -1, True, 1048577, "8192"])
async def test_token_limit_rejected_before_transport(tokens):
    m = DeepSeekModel(host=REVIEWED_HOST, credential="test-only", transport="go")
    with pytest.raises(RuntimeFailure, match="invalid_model_token_limit"):
        await m.complete([], [], tokens)
    assert m.connection.process is None


def test_explicit_transport_no_fallback(monkeypatch):
    monkeypatch.delenv("AIAB_DEEPSEEK_TRANSPORT", raising=False)
    m = DeepSeekModel(host=REVIEWED_HOST, credential="test-only")
    assert m.transport == "python"
    with pytest.raises(RuntimeFailure, match="unapproved_model_transport"):
        DeepSeekModel(host=REVIEWED_HOST, credential="test-only", transport="http")
    with pytest.raises(RuntimeFailure, match="unapproved_go_enclave_host"):
        DeepSeekModel(host="other.tinfoil.dev", credential="test-only", transport="go")
    bad = copy.deepcopy(MODEL_PROFILE)
    bad["release_digest"] = "sha256:other"
    with pytest.raises(RuntimeFailure, match="unapproved_model_binding"):
        DeepSeekModel(host=REVIEWED_HOST, credential="test-only", transport="go", profile=bad)


def test_current_vllm_reasoning_wire_alias_preserved_privately():
    result = {
        "model": module.MODEL_ID,
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": "synthetic",
                    "reasoning": "private-placeholder",
                },
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }
    answer = module.validate_completion(result)
    pi = module.pi_completion(answer)
    messages = module.wire_messages([{"role": "assistant", **pi}], "fixed")
    assert messages[-1]["reasoning"] == "private-placeholder"
    result["choices"][0]["message"]["reasoning_content"] = "conflicting"
    with pytest.raises(RuntimeFailure, match="invalid_deepseek_completion"):
        module.validate_completion(result)


@pytest.mark.parametrize(
    "result",
    [
        [],
        {},
        {"model": module.MODEL_ID, "choices": [{}]},
        {"model": module.MODEL_ID, "choices": [{"finish_reason": "length"}]},
    ],
)
def test_malformed_or_truncated_completion_fails_closed(result):
    with pytest.raises(RuntimeFailure, match="invalid_deepseek_completion"):
        module.validate_completion(result)


def test_released_encoder_pin_and_tool_round_order():
    import hashlib

    from agent_runtime.verified_transport import render_prompt

    encoder = module.BROKER.parent / "deepseek_v41_encoding.py"
    assert hashlib.sha256(encoder.read_bytes()).hexdigest() == (
        "081382cb953a1c8041fdfc82a6001cf0c932553171436625c89fe5b9fbcda464"
    )
    calls = [
        {
            "id": id,
            "type": "function",
            "function": {"name": "coverage", "arguments": '{"request":"{}"}'},
        }
        for id in ("one", "two")
    ]
    prompt = render_prompt(
        [
            {"role": "user", "content": "synthetic"},
            {
                "role": "assistant",
                "content": None,
                "reasoning": "private-placeholder",
                "tool_calls": calls,
            },
            {"role": "tool", "tool_call_id": "two", "content": "second-result"},
            {"role": "tool", "tool_call_id": "one", "content": "first-result"},
        ],
        [],
    )
    assert "private-placeholder</think>" in prompt
    assert prompt.index("first-result") < prompt.index("second-result")
    assert 'name="coverage"' in prompt
    assert "25" in prompt


async def test_context_limit_denies_inference():
    class Connection:
        async def request(self, *a, **kw):
            return 200, {"count": MODEL_PROFILE["context_tokens"]}

    m = DeepSeekModel(host=REVIEWED_HOST, credential="test-only", transport="python")
    m.connection = Connection()
    with pytest.raises(RuntimeFailure, match="model_context_exhausted"):
        await m.count_tokens([], [])
