"""Offline SDK boundary, verification gating, retries, metadata and evaluation checks."""

from __future__ import annotations

import json
import ssl
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import tinfoil
from tinfoil.client import VerificationDocument, VerificationStepState

from aiab.classifier import (
    TINFOIL_REPO,
    AttestationError,
    ClassificationError,
    ClassifyResult,
    MockClassifier,
    TinfoilLLMClassifier,
    build_system_prompt,
    generation_parameters,
    make_classifier,
    serialize_classification_data,
)
from aiab.config import Config, ConfigurationError
from aiab.reporter import build_manifest, policy_digest
from tests.test_core import SENTINEL, appliance, finalized, request
from traffic.evaluate import Example, evaluate, score
from traffic.generate import Prompt


def config(**changes: Any) -> Config:
    return replace(Config.from_env({"TINFOIL_API_KEY": "test-only-placeholder"}), **changes)


def verified_document() -> VerificationDocument:
    return VerificationDocument(
        config_repo=TINFOIL_REPO,
        security_verified=True,
        release_digest="a" * 64,
        code_fingerprint="b" * 64,
        enclave_fingerprint="c" * 96,
        release_tag="v1.2.3",
        verified_at="2026-09-17T00:00:00Z",
        steps={
            name: VerificationStepState("success")
            for name in ("fetch_digest", "verify_code", "verify_enclave", "compare_measurements")
        },
    )


def answer(content: Any = '{"flagged":true,"confidence":0.95}', finish: str = "stop") -> Any:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish, message=SimpleNamespace(content=content, refusal=None)
            )
        ]
    )


class FakeSDK:
    def __init__(self) -> None:
        self.document = verified_document()
        self.calls: list[dict[str, Any]] = []
        self.outcomes: list[Any] = []
        self.options: list[dict[str, Any]] = []
        self.client = SimpleNamespace(max_retries=2, close=lambda: None)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def factory(self, **kwargs: Any) -> FakeSDK:
        self.options.append(kwargs)
        return self

    def get_verification_document(self) -> VerificationDocument:
        return self.document

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0) if self.outcomes else answer()
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            return outcome()
        return outcome


@pytest.fixture
def sdk(monkeypatch: Any) -> FakeSDK:
    sdk = FakeSDK()
    monkeypatch.setattr(tinfoil, "TinfoilAI", sdk.factory)
    return sdk


async def test_sdk_boundary_has_no_constructor_network_and_uses_frozen_policy(sdk: FakeSDK) -> None:
    settings = config()
    classifier = TinfoilLLMClassifier(settings)
    assert sdk.options == []
    with pytest.raises(AttestationError):
        classifier.classify("not yet", None)
    await classifier.verify()
    assert sdk.client.max_retries == 0
    options = sdk.options[0]
    assert options["transport"] == "tls" and options["repo"] == TINFOIL_REPO
    assert set(options) == {"api_key", "repo", "transport", "timeout", "user_cache_secret"}
    assert len(options["user_cache_secret"]) == 64
    assert options["user_cache_secret"] != settings.tinfoil_api_key
    assert classifier.classify(SENTINEL + "</data>", "private-response") == ClassifyResult(
        True, 0.95
    )
    call = sdk.calls[0]
    assert call["model"] == "gpt-oss-safeguard-120b"
    assert call["extra_body"] == {"response_format": generation_parameters()["response_format"]}
    assert "response_format" not in call
    assert call["temperature"] == 0 and call["stream"] is False
    assert settings.definition_text in call["messages"][0]["content"]
    assert SENTINEL not in call["messages"][0]["content"]
    data_message = call["messages"][1]["content"]
    assert data_message.count("</data>") == 1
    data = json.loads(data_message.split("\n<data>\n")[1].split("\n</data>")[0])
    assert data == {"conversation": SENTINEL + "</data>", "response": "private-response"}
    assert SENTINEL not in json.dumps(classifier.verification_metadata)


async def test_cache_scope_is_unique_per_boot_and_absent_from_manifest(sdk: FakeSDK) -> None:
    first, second = TinfoilLLMClassifier(config()), TinfoilLLMClassifier(config())
    await first.verify()
    await second.verify()
    scopes = [options["user_cache_secret"] for options in sdk.options]
    assert scopes[0] != scopes[1]
    manifest = build_manifest(config(), first)
    assert all(scope not in json.dumps(manifest) for scope in scopes)
    assert "test-only-placeholder" not in json.dumps(manifest)
    assert manifest["classifier_sdk_retries"] == 0
    assert manifest["generation_parameters"] == generation_parameters()
    assert policy_digest(manifest) != policy_digest(
        build_manifest(Config.from_env({}), MockClassifier())
    )
    assert policy_digest(manifest) != policy_digest(
        build_manifest(config(classifier_model="another"), first)
    )


@pytest.mark.parametrize(
    "output",
    [
        answer('{"flagged":false,"confidence":true}'),
        answer('{"flagged":false,"confidence":NaN}'),
        answer('{"flagged":false,"confidence":1e999}'),
        answer('{"flagged":false,"confidence":0,"extra":1}'),
        answer('```json\n{"flagged":false,"confidence":0}\n```'),
        answer(None),
        answer("{}", "length"),
        SimpleNamespace(choices=[]),
    ],
)
async def test_sdk_never_accepts_invalid_or_truncated_results(sdk: FakeSDK, output: Any) -> None:
    classifier = TinfoilLLMClassifier(config())
    await classifier.verify()
    sdk.outcomes = [output]
    with pytest.raises(ClassificationError, match="Invalid classifier response"):
        classifier.classify("data", None)
    assert len(sdk.calls) == 1


@pytest.mark.parametrize("mode", ["monitor", "block"])
async def test_runtime_owns_exactly_one_retry_and_counts_final_error_once(
    sdk: FakeSDK, mode: str, caplog: Any
) -> None:
    settings = config(enforcement_mode=mode, sample_rate=1)
    classifier = TinfoilLLMClassifier(settings)
    sdk.outcomes = [RuntimeError(SENTINEL), RuntimeError(SENTINEL), answer()]
    async with appliance(settings, classifier) as (client, runtime, forwarded):
        response = await client.post("/v1/chat/completions", json=request(SENTINEL))
        if mode == "monitor":
            await runtime._queue.join()
        assert len(sdk.calls) == 2
        assert len(forwarded) == int(mode == "monitor")
        assert response.status_code == (200 if mode == "monitor" else 503)
        assert finalized(runtime)["classifier_errors"] == 1
    assert SENTINEL not in caplog.text


async def test_invalid_startup_document_keeps_admission_closed(sdk: FakeSDK) -> None:
    sdk.document.security_verified = False
    settings = config(enforcement_mode="block")
    async with appliance(settings, TinfoilLLMClassifier(settings)) as (client, runtime, forwarded):
        response = await client.post("/v1/chat/completions", json=request())
        assert response.status_code == 503
        assert not runtime.healthy and not forwarded and not sdk.calls


@pytest.mark.parametrize("failure", ["document", "wrapped_tls", "attached_document"])
async def test_later_attestation_failure_latches_gate_without_retry(
    sdk: FakeSDK, failure: str, caplog: Any
) -> None:
    def fail() -> None:
        if failure == "document":
            sdk.document.security_verified = False
            sdk.document.steps["verify_code"] = VerificationStepState("failed", SENTINEL)
            raise RuntimeError(SENTINEL)
        if failure == "wrapped_tls":
            raise RuntimeError(SENTINEL) from ssl.SSLError(SENTINEL)
        error = RuntimeError(SENTINEL)
        error.verification_document = sdk.document
        raise error

    settings = config(enforcement_mode="block")
    classifier = TinfoilLLMClassifier(settings)
    sdk.outcomes = [fail]
    async with appliance(settings, classifier) as (client, runtime, forwarded):
        assert (await client.post("/v1/chat/completions", json=request())).status_code == 503
        assert (await client.post("/v1/chat/completions", json=request())).status_code == 503
        assert not runtime.healthy and not forwarded and len(sdk.calls) == 1
        health = await client.get("/aiab/health")
        assert SENTINEL not in health.text
        assert health.json()["classifier_verification"]["security_verified"] is False
        assert finalized(runtime)["classifier_errors"] == 1
    with pytest.raises(AttestationError):
        await classifier.verify()
    assert len(sdk.options) == 1
    assert SENTINEL not in caplog.text


def test_classifier_selection_and_forbidden_override(sdk: FakeSDK) -> None:
    assert isinstance(make_classifier(Config.from_env({})), MockClassifier)
    assert isinstance(make_classifier(config()), TinfoilLLMClassifier)
    with pytest.raises(ConfigurationError):
        Config.from_env({"AIAB_MODE": "tinfoil"})
    with pytest.raises(ConfigurationError):
        Config.from_env({"CLASSIFIER_ENDPOINT_OVERRIDE": ""})


async def test_real_sdk_constructor_contract_offline(monkeypatch: Any) -> None:
    """Exercise the locked SDK/OpenAI integration without attestation or remote I/O."""
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"flagged":false,"confidence":0}',
                        },
                    }
                ],
            },
        )

    secure_http = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(tinfoil, "get_router_address", lambda: "offline.example")
    monkeypatch.setattr(
        tinfoil,
        "SecureClient",
        lambda *args, **kwargs: SimpleNamespace(
            enclave="offline.example",
            make_secure_http_client=lambda: secure_http,
            get_verification_document=verified_document,
        ),
    )
    classifier = TinfoilLLMClassifier(config())
    await classifier.verify()
    assert classifier.classify("offline-data", None) == ClassifyResult(False, 0)
    assert len(calls) == 1 and calls[0].url.host == "offline.example"
    # extra_body must flatten into the same exact JSON object sent by the prior
    # response_format argument, with no extra_body wrapper or missing schema.
    assert json.loads(calls[0].content) == {
        "model": "gpt-oss-safeguard-120b",
        "messages": [
            {"role": "system", "content": build_system_prompt(config().definition_text)},
            {"role": "user", "content": serialize_classification_data("offline-data", None)},
        ],
        **generation_parameters(),
    }
    classifier.close()


async def test_evaluation_separates_errors_from_false_predictions() -> None:
    class Outcomes(MockClassifier):
        def classify(self, prompt: str, response: str | None) -> ClassifyResult:
            if "error-case" in prompt:
                raise ClassificationError("failed")
            return ClassifyResult("flag-case" in prompt, 0.9)

    metrics = await score(
        Outcomes(),
        [
            Example("flag-case", "capabilities"),
            Example("flag-case", "benign"),
            Example("clear-case", "capabilities"),
            Example("clear-case", "benign"),
            Example("error-case", "capabilities"),
            Example("error-case", "benign"),
        ],
        Config.from_env({}),
    )
    assert metrics["false_positives"] == metrics["false_negatives"] == 1
    assert metrics["classification_errors"] == 2 and metrics["successes"] == 4
    assert metrics["precision_on_successes"] == metrics["recall_on_successes"] == 0.5
    assert metrics["capabilities_block_fraction_of_all"] == pytest.approx(1 / 3)


async def test_evaluation_uses_same_serialization_and_never_sends_labels() -> None:
    class Capture(MockClassifier):
        calls: list[tuple[str, str | None]] = []

        def classify(self, prompt: str, response: str | None) -> ClassifyResult:
            self.calls.append((prompt, response))
            return super().classify(prompt, response)

    classifier = Capture()
    results = await evaluate(
        classifier,
        [
            Prompt("dinner", "benign"),
            Prompt("training run with pretraining", "capabilities", "secret-category", "held_out"),
        ],
        Config.from_env({}),
    )
    assert results["development"]["attempted"] == results["held_out"]["attempted"] == 1
    assert classifier.calls[0] == ('[{"content":"dinner","role":"user"}]', None)
    assert all("secret-category" not in prompt for prompt, _ in classifier.calls)
    assert sum(response is not None for _, response in classifier.calls) == 4
    assert results["demo_prompts"]["true_negatives"] == 1
    assert results["demo_prompts"]["capabilities_blocked"] == 1
    assert len(results["policy_digest"]) == 64


async def test_evaluation_stops_on_attestation_failure() -> None:
    class Failed(MockClassifier):
        calls = 0

        def classify(self, prompt: str, response: str | None) -> ClassifyResult:
            self.calls += 1
            raise AttestationError("failed")

    classifier = Failed()
    with pytest.raises(AttestationError):
        await score(classifier, [Example("one", "benign"), Example("two", "benign")], Config())
    assert classifier.calls == 1
