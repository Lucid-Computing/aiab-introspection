"""Boundary behavior of the real adapter, with an explicit transport test double."""

import pytest

from aiab.audit.model import MODEL_BINDINGS, PinnedModel
from aiab.audit.protocol import AuditError, decode


class TransportDouble:
    def __init__(self, count=10, content='{"score":0.9}', finish="stop"):
        self.calls = []
        self.count, self.content, self.finish = count, content, finish

    async def request(self, method, path, body, token):
        self.calls.append((path, decode(body)))
        if path == "/tokenize":
            return 200, {"count": self.count}
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


async def test_exact_context_overflow_never_calls_inference():
    transport = TransportDouble(count=769)
    model, settings = adapter(transport)
    with pytest.raises(AuditError, match="input_too_large"):
        await model.classify("all original text", POLICY, settings)
    assert [path for path, _ in transport.calls] == ["/tokenize"]


async def test_complete_text_and_policy_used_once_without_truncation():
    transport = TransportDouble()
    model, settings = adapter(transport)
    text = "first prompt\nsecond prompt\nthird tool result"
    assert await model.classify(text, POLICY, settings) == 0.9
    assert len(transport.calls) == 2
    tokenized, inferred = [body for _, body in transport.calls]
    assert tokenized["messages"] == inferred["messages"]
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
    assert len(transport.calls) == 2


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
    assert len(transport.calls) == 2
