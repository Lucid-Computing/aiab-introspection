"""Boundary behavior of the real adapter, with an explicit transport test double."""

import pytest

from aiab.audit import model as model_module
from aiab.audit.model import MODEL_BINDINGS, PinnedModel
from aiab.audit.protocol import AuditError, decode


class TransportDouble:
    def __init__(self, content='{"score":0.9}', finish="stop", fail=False):
        self.calls = []
        self.content, self.finish, self.fail = content, finish, fail
        self.failed = False
        self.started = self.closed = False

    async def start(self):
        self.started = True

    async def close(self):
        self.closed = True

    async def request(self, method, path, body, token):
        self.calls.append((path, decode(body)))
        if self.fail:
            # As the real connection does when the router's key changed mid-life.
            self.failed = True
            raise AuditError("model_unavailable", 503)
        return 200, {
            "choices": [{"finish_reason": self.finish, "message": {"content": self.content}}]
        }


def adapter(transport):
    (repo, release), (name, artifact) = next(iter(MODEL_BINDINGS.items()))
    settings = {
        "repo": repo,
        "release_digest": release,
        "id": name,
        "artifact_digest": artifact,
        "host": "example.tinfoil.dev",
        "context_tokens": 1024,
        "max_output_tokens": 256,
        "temperature": 0,
    }
    model = PinnedModel(settings, "test-token")
    model.connection = transport
    return model, settings


POLICY = {
    "problem": "RSI",
    "prompt": "classify",
    "positive_examples": ["launch"],
    "negative_examples": ["explain"],
}


async def test_possible_context_overflow_never_calls_inference():
    transport = TransportDouble()
    model, settings = adapter(transport)
    # 1024-token context, 256 for the answer: this many bytes may not fit, so it is refused.
    with pytest.raises(AuditError, match="input_too_large"):
        await model.classify("x" * 700, POLICY, settings)
    assert transport.calls == []


async def test_context_bound_counts_every_byte_of_every_message():
    model, settings = adapter(TransportDouble())
    budget = settings["context_tokens"] - settings["max_output_tokens"]
    fixed = model_module.PROMPT_OVERHEAD_TOKENS + 2 * model_module.MESSAGE_OVERHEAD_TOKENS
    fixed += len("system") + len("user")
    fits = budget - fixed
    await model.check_messages(
        [{"role": "system", "content": "s" * (fits - 1)}, {"role": "user", "content": "u"}],
        settings["max_output_tokens"],
    )
    with pytest.raises(AuditError, match="input_too_large"):
        # A two-byte character counts as two possible tokens.
        await model.check_messages(
            [{"role": "system", "content": "s" * (fits - 1)}, {"role": "user", "content": "é"}],
            settings["max_output_tokens"],
        )


async def test_complete_text_and_policy_used_once_without_truncation():
    transport = TransportDouble()
    model, settings = adapter(transport)
    text = "first prompt\nsecond prompt\nthird tool result"
    assert await model.classify(text, POLICY, settings) == 0.9
    assert [path for path, _ in transport.calls] == ["/v1/chat/completions"]
    ((_, inferred),) = transport.calls
    assert decode(inferred["messages"][1]["content"])["audit_text"] == text
    assert inferred["reasoning_effort"] == "low"
    assert inferred["max_tokens"] == settings["max_output_tokens"]
    assert "truncate_prompt_tokens" not in inferred


@pytest.mark.parametrize(
    "content,finish",
    [
        ('{"score":0.9,"raw":"private"}', "stop"),
        ('{"score":NaN}', "stop"),
        ('{"score":true}', "stop"),
        ('{"score":0.9}', "length"),
        ("not JSON", "stop"),
    ],
)
async def test_invalid_classifier_output_never_retries(content, finish):
    transport = TransportDouble(content=content, finish=finish)
    model, settings = adapter(transport)
    with pytest.raises(AuditError):
        await model.classify("private", POLICY, settings)
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "finish,content,code",
    [
        ("length", "", "model_output_truncated"),
        ("stop", "not JSON", "model_output_invalid"),
        ("stop", '{"flagged":false}', "model_output_invalid"),
    ],
)
async def test_response_failure_is_actionable_without_releasing_model_text(finish, content, code):
    transport = TransportDouble(content=content, finish=finish)
    model, settings = adapter(transport)
    with pytest.raises(AuditError) as failure:
        await model.classify("private", POLICY, settings)
    assert failure.value.code == code
    assert len(transport.calls) == 1


async def test_router_key_change_reattests_before_sending_again(monkeypatch):
    stale = TransportDouble(fail=True)
    fresh = TransportDouble()
    made = []

    def connect(host, model_id):
        made.append((host, model_id))
        return fresh

    model, settings = adapter(stale)
    monkeypatch.setattr(model_module, "RoutedConnection", connect)
    assert await model.classify("private", POLICY, settings) == 0.9
    assert stale.closed and fresh.started
    assert made == [(settings["host"], settings["id"])]
    assert len(stale.calls) == len(fresh.calls) == 1


async def test_reattestation_is_tried_once(monkeypatch):
    attempts = []

    def connect(host, model_id):
        attempts.append(TransportDouble(fail=True))
        return attempts[-1]

    model, settings = adapter(TransportDouble(fail=True))
    monkeypatch.setattr(model_module, "RoutedConnection", connect)
    with pytest.raises(AuditError, match="model_unavailable"):
        await model.classify("private", POLICY, settings)
    assert len(attempts) == 1
