"""Exercise the privacy boundary, admission accounting, and local policy enforcement."""

from __future__ import annotations

import asyncio
import base64
import gzip
import json
import logging
import ssl
import threading
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from aiab.classifier import (
    AttestationError,
    ClassificationError,
    ClassifyResult,
    MockClassifier,
    parse_classify_result,
    serialize_messages,
)
from aiab.config import Config, ConfigurationError
from aiab.keys import ephemeral_tls_files
from aiab.proxy import Runtime, create_app
from aiab.reporter import REPORT_FIELDS, Reporter, build_manifest, canonical_json, policy_digest
from aiab.sampler import Sampler

SENTINEL = "private-sentinel-27ff-secret-input"
CAPABILITIES = "Plan pretraining with a learning rate schedule and MoE routing."


def settings(**changes: Any) -> Config:
    return replace(Config.from_env({}), **changes)


def request(text: str = "Suggest dinner") -> dict[str, Any]:
    return {
        "model": "local-model",
        "messages": [{"role": "user", "content": text}],
        "stream": False,
    }


@asynccontextmanager
async def appliance(
    config: Config | None = None,
    classifier: Any = None,
    handler: Any = None,
) -> AsyncIterator[tuple[httpx.AsyncClient, Runtime, list[httpx.Request]]]:
    upstream_requests: list[httpx.Request] = []

    async def upstream(req: httpx.Request) -> httpx.Response:
        upstream_requests.append(req)
        if handler:
            return await handler(req)
        return httpx.Response(200, json={"choices": [{"message": {"content": "A tasty menu"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as upstream_client:
        app = create_app(
            config or settings(), classifier=classifier, upstream_client=upstream_client
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://localhost"
            ) as client:
                yield client, app.state.runtime, upstream_requests


def finalized(runtime: Runtime) -> dict[str, Any]:
    runtime.reporter.clock = lambda: (
        runtime.reporter._started
        + (runtime.config.report_period_seconds + runtime.config.processing_timeout_seconds + 0.01)
    )
    return json.loads(runtime.reporter.reports[-1]["report"])


def test_configuration_is_frozen_and_rejects_override() -> None:
    config = Config.from_env({})
    with pytest.raises(FrozenInstanceError):
        config.sample_rate = 1
    with pytest.raises(ConfigurationError, match="CLASSIFIER_ENDPOINT_OVERRIDE"):
        Config.from_env({"CLASSIFIER_ENDPOINT_OVERRIDE": ""})
    for env in ({"SAMPLE_RATE": "nan"}, {"BLOCK_THRESHOLD": "inf"}, {"LISTEN_PORT": "0"}):
        with pytest.raises(ConfigurationError):
            Config.from_env(env)
    with pytest.raises(ConfigurationError):
        create_app(settings(aiab_mode="tinfoil"))
    with pytest.raises(ConfigurationError):
        create_app(settings(tinfoil_api_key="never-fallback"))


@pytest.mark.parametrize(
    "value",
    [
        '{"flagged": true, "confidence": 0.95}',
        '{"confidence": 0, "flagged": false}',
        '{"confidence": 1, "flagged": true}',
    ],
)
def test_strict_classifier_json_valid(value: str) -> None:
    assert isinstance(parse_classify_result(value), ClassifyResult)


@pytest.mark.parametrize(
    "value",
    [
        "not JSON",
        '```json\n{"flagged":false,"confidence":0}\n```',
        '{"flagged":"false","confidence":0}',
        '{"flagged":0,"confidence":0}',
        '{"flagged":false,"confidence":true}',
        '{"flagged":false,"confidence":"0"}',
        '{"flagged":false,"confidence":NaN}',
        '{"flagged":false,"confidence":Infinity}',
        '{"flagged":false,"confidence":1e999}',
        '{"flagged":false,"confidence":-0.1}',
        '{"flagged":false,"confidence":1.1}',
        '{"flagged":false,"confidence":0,"extra":1}',
        '{"flagged":true,"flagged":false,"confidence":0}',
        '{"flagged":false}',
        "[]",
    ],
)
def test_strict_classifier_json_invalid(value: str) -> None:
    with pytest.raises(ClassificationError):
        parse_classify_result(value)


def test_sampler_rate_has_loose_bounds() -> None:
    sampler = Sampler(0.2)
    observed = sum(sampler.should_sample(str(i)) for i in range(10_000))
    assert 1600 < observed < 2400
    assert not Sampler(0).should_sample("same-id")
    assert Sampler(1).should_sample("same-id")


def test_mock_term_families_and_response() -> None:
    classifier = MockClassifier()
    assert classifier.classify("pretraining", None) == ClassifyResult(True, 0.6)
    assert classifier.classify("dinner", "fine-tuning and reward model") == ClassifyResult(
        True, 0.95
    )
    assert classifier.classify("dinner", None) == ClassifyResult(False, 0.0)


def test_report_schema_signature_empty_periods_history_and_deadlines() -> None:
    now = [0.0]
    config = settings(report_period_seconds=10, report_history=2, processing_timeout_seconds=5)
    reporter = Reporter(
        config, MockClassifier(), clock=lambda: now[0], wall_time=datetime(2026, 1, 1, tzinfo=UTC)
    )
    ticket = reporter.admit(selected=True)
    assert reporter.forwarded(ticket)
    now[0] = 10
    assert reporter.reports == []  # No partial period is represented as final.
    now[0] = 15
    envelope = reporter.reports[0]
    report = json.loads(envelope["report"])
    assert set(report) == REPORT_FIELDS
    assert envelope["report"] == canonical_json(report)
    public = Ed25519PublicKey.from_public_bytes(base64.b64decode(reporter.signing_key.public_key))
    public.verify(base64.b64decode(envelope["signature"]), envelope["report"].encode())
    assert (
        report["requests_total"] == report["requests_selected"] == report["classifier_errors"] == 1
    )
    assert report["requests_classified"] == 0
    assert not reporter.classified(ticket, ClassifyResult(True, 0.95))
    assert reporter.reports[0] == envelope  # Late completions cannot revise signed history.
    now[0] = 35
    reports = [json.loads(item["report"]) for item in reporter.reports]
    assert [item["sequence"] for item in reports] == [2, 3]
    assert all(item["requests_total"] == 0 for item in reports)
    assert reports[0]["period_end"] == reports[1]["period_start"]


def test_policy_digest_changes_and_secret_origin_redaction() -> None:
    config = settings(inference_url="https://username:credential-sentinel@inference.test:8443")
    baseline = build_manifest(config, MockClassifier())
    assert baseline["inference_origin"] == "https://inference.test:8443"
    assert "credential-sentinel" not in canonical_json(baseline)
    for change in (
        {"block_threshold": 0.9},
        {"definition_text": config.definition_text + "\n"},
        {"enforcement_mode": "block"},
        {"sample_rate": 0.5},
        {"processing_timeout_seconds": 12},
    ):
        assert policy_digest(
            build_manifest(replace(config, **change), MockClassifier())
        ) != policy_digest(baseline)
    assert Reporter(config, MockClassifier()).boot_id != Reporter(config, MockClassifier()).boot_id


async def test_block_mode_rejects_capabilities_and_forwards_benign() -> None:
    async with appliance(settings(enforcement_mode="block")) as (client, runtime, forwarded):
        response = await client.post("/v1/chat/completions", json=request(CAPABILITIES))
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "aiab_blocked"
        assert forwarded == []
        benign = await client.post("/v1/chat/completions", json=request())
        assert benign.status_code == 200
        assert len(forwarded) == 1
        report = finalized(runtime)
        assert (
            report["requests_total"]
            == report["requests_selected"]
            == report["requests_classified"]
            == 2
        )
        assert (
            report["requests_forwarded"]
            == report["requests_blocked"]
            == report["requests_flagged"]
            == 1
        )


@pytest.mark.parametrize("mode", ["monitor", "block"])
async def test_sentinel_never_enters_logs_or_audit_artifacts(mode: str, caplog: Any) -> None:
    caplog.set_level(logging.DEBUG)
    async with appliance(settings(enforcement_mode=mode, sample_rate=1)) as (client, runtime, _):
        assert (
            await client.post("/v1/chat/completions", json=request(SENTINEL))
        ).status_code == 200
        await runtime._queue.join()
        finalized(runtime)
        artifacts = [
            (await client.get(path)).text
            for path in (
                "/aiab/manifest",
                "/aiab/pubkey",
                "/aiab/reports",
                "/aiab/reports/latest",
                "/aiab/health",
            )
        ]
        assert SENTINEL not in caplog.text
        assert all(SENTINEL not in artifact for artifact in artifacts)
        assert all("request_id" not in artifact for artifact in artifacts)


class BrokenClassifier(MockClassifier):
    def __init__(self) -> None:
        self.calls = 0

    async def classify(self, prompt: str, response: str | None) -> ClassifyResult:
        self.calls += 1
        raise RuntimeError(SENTINEL)


@pytest.mark.parametrize("mode", ["monitor", "block"])
async def test_final_failure_counted_once_after_retry(mode: str, caplog: Any) -> None:
    classifier = BrokenClassifier()
    async with appliance(settings(enforcement_mode=mode, sample_rate=1), classifier) as (
        client,
        runtime,
        forwarded,
    ):
        response = await client.post("/v1/chat/completions", json=request(SENTINEL))
        await runtime._queue.join()
        assert response.status_code == (503 if mode == "block" else 200)
        assert len(forwarded) == (0 if mode == "block" else 1)
        assert classifier.calls == 2
        report = finalized(runtime)
        assert report["classifier_errors"] == report["requests_selected"] == 1
        assert report["requests_classified"] == 0
        assert SENTINEL not in caplog.text


async def test_upstream_failure_still_selected_and_accounted() -> None:
    async def broken(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(SENTINEL)

    async with appliance(settings(sample_rate=1), handler=broken) as (client, runtime, _):
        assert (await client.post("/v1/chat/completions", json=request())).status_code == 502
        report = finalized(runtime)
        assert (
            report["requests_forwarded"]
            == report["requests_selected"]
            == report["classifier_errors"]
            == 1
        )


@pytest.mark.parametrize("path", ["/v1/chat/completions", "/v1/models"])
async def test_upstream_headers_status_and_decoded_body_preserved(path: str) -> None:
    body = b'{"error":{"message":"Too many requests"}}'
    compressed = gzip.compress(body)

    async def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            content=compressed,
            headers=[
                ("content-type", "application/json; charset=utf-8"),
                ("content-encoding", "gzip"),
                ("content-length", str(len(compressed))),
                ("retry-after", "30"),
                ("x-request-id", "upstream-request"),
                ("x-ratelimit-remaining-requests", "0"),
                ("www-authenticate", 'Bearer realm="inference"'),
                ("set-cookie", "a=1"),
                ("set-cookie", "b=2"),
                ("connection", "keep-alive, X-Internal"),
                ("keep-alive", "timeout=10"),
                ("x-internal", "must-not-escape"),
                ("vary", "Accept-Encoding"),
            ],
        )

    async with appliance(settings(cors_allow_origin="http://localhost:8081"), handler=upstream) as (
        client,
        _,
        _,
    ):
        headers = {"origin": "http://localhost:8081"}
        response = (
            await client.post(path, json=request(), headers=headers)
            if path.endswith("completions")
            else await client.get(path, headers=headers)
        )
        assert response.status_code == 429
        assert response.content == body
        assert response.headers["content-type"] == "application/json; charset=utf-8"
        assert response.headers["content-length"] == str(len(body))
        assert response.headers["retry-after"] == "30"
        assert response.headers["x-request-id"] == "upstream-request"
        assert response.headers["x-ratelimit-remaining-requests"] == "0"
        assert response.headers["www-authenticate"] == 'Bearer realm="inference"'
        assert response.headers.get_list("set-cookie") == ["a=1", "b=2"]
        assert response.headers["vary"] == "Accept-Encoding, Origin"
        for name in ("content-encoding", "connection", "keep-alive", "x-internal"):
            assert name not in response.headers


async def test_full_message_list_classified_including_system_and_assistant() -> None:
    messages = [
        {"role": "system", "content": "Plan pretraining."},
        {"role": "developer", "content": [{"type": "text", "text": "Use a learning rate."}]},
        {"role": "assistant", "content": "We are improving a model."},
        {"role": "user", "content": "Continue."},
    ]
    assert json.loads(serialize_messages(messages)) == messages
    async with appliance(settings(enforcement_mode="block")) as (client, _, forwarded):
        response = await client.post(
            "/v1/chat/completions", json={**request(), "messages": messages}
        )
        assert response.status_code == 403
        assert forwarded == []


@pytest.mark.parametrize(
    "change",
    [
        {"stream": True},
        {"stream": "false"},
        {"tools": []},
        {"functions": []},
        {"messages": [{"role": "tool", "content": "text"}]},
        {
            "messages": [
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}
            ]
        },
        {"messages": [{"role": "user", "content": "hello", "attachments": ["x"]}]},
        {"messages": []},
        {"messages": [{"role": "user", "content": None}]},
    ],
)
async def test_unsupported_requests_do_not_enter_counters(change: dict[str, Any]) -> None:
    async with appliance() as (client, runtime, forwarded):
        assert (
            await client.post("/v1/chat/completions", json={**request(), **change})
        ).status_code == 400
        assert forwarded == []
        assert finalized(runtime)["requests_total"] == 0


async def test_cors_paths_models_and_no_extra_api() -> None:
    async with appliance(settings(cors_allow_origin="http://localhost:8081")) as (
        client,
        runtime,
        _,
    ):
        headers = {"origin": "http://localhost:8081"}
        for path in ("/v1/models", "/aiab/health"):
            response = await client.get(path, headers=headers)
            assert response.headers["access-control-allow-origin"] == headers["origin"]
        response = await client.options("/v1/chat/completions", headers=headers)
        assert response.status_code == 204
        assert response.headers["access-control-allow-methods"] == "POST, OPTIONS"
        for method in (client.get, client.options):
            response = await method("/v1/unknown", headers=headers)
            assert response.status_code == 404
            assert "access-control-allow-origin" not in response.headers
        response = await client.options(
            "/v1/models", headers={**headers, "access-control-request-method": "POST"}
        )
        assert response.status_code == 405
        assert "access-control-allow-origin" not in response.headers
        response = await client.post("/v1/models", headers=headers)
        assert response.status_code == 405
        assert "access-control-allow-origin" not in response.headers
        response = await client.get("/aiab/reports", headers=headers)
        assert "access-control-allow-origin" not in response.headers
        response = await client.get("/v1/models", headers={"origin": "https://other.test"})
        assert "access-control-allow-origin" not in response.headers
        for path in ("/docs", "/openapi.json", "/anything", "/v1/models/"):
            assert (await client.get(path)).status_code == 404
        assert finalized(runtime)["requests_total"] == 0


class SlowClassifier(MockClassifier):
    async def classify(self, prompt: str, response: str | None) -> ClassifyResult:
        await asyncio.sleep(10)
        return ClassifyResult(False, 0)


@pytest.mark.parametrize("mode", ["monitor", "block"])
async def test_sync_method_returning_awaitable_retains_concurrency_slot(mode: str) -> None:
    class AwaitableClassifier(MockClassifier):
        active = 0
        maximum_active = 0
        calls = 0

        def classify(self, prompt: str, response: str | None) -> Awaitable[ClassifyResult]:
            async def complete() -> ClassifyResult:
                self.calls += 1
                self.active += 1
                self.maximum_active = max(self.maximum_active, self.active)
                try:
                    await asyncio.sleep(0.03)
                    return ClassifyResult(False, 0)
                finally:
                    self.active -= 1

            return complete()

    classifier = AwaitableClassifier()
    config = settings(
        enforcement_mode=mode, sample_rate=1, classifier_concurrency=1, monitor_workers=2
    )
    async with appliance(config, classifier) as (client, runtime, forwarded):
        responses = await asyncio.gather(
            *(client.post("/v1/chat/completions", json=request()) for _ in range(2))
        )
        await runtime._queue.join()
        assert all(response.status_code == 200 for response in responses)
        assert len(forwarded) == classifier.calls == 2
        assert classifier.maximum_active == 1
        assert classifier.active == 0
        assert finalized(runtime)["requests_classified"] == 2


async def test_cancelled_sync_invocation_retains_slot_until_thread_exits() -> None:
    released = threading.Event()

    class BlockingClassifier(MockClassifier):
        calls = 0

        def classify(self, prompt: str, response: str | None) -> Awaitable[ClassifyResult]:
            self.calls += 1
            released.wait(timeout=2)

            async def complete() -> ClassifyResult:
                return ClassifyResult(False, 0)

            return complete()

    classifier = BlockingClassifier()
    config = settings(
        enforcement_mode="block",
        classifier_concurrency=1,
        classifier_attempts=1,
        classifier_attempt_timeout_seconds=0.02,
    )
    async with appliance(config, classifier) as (client, runtime, forwarded):
        try:
            responses = await asyncio.gather(
                *(client.post("/v1/chat/completions", json=request()) for _ in range(2))
            )
            assert all(response.status_code == 503 for response in responses)
            assert not forwarded
            assert classifier.calls == 1
            assert runtime._slots.locked()
        finally:
            released.set()
            await asyncio.gather(*runtime._calls, return_exceptions=True)
        assert not runtime._slots.locked()


@pytest.mark.parametrize("mode", ["monitor", "block"])
async def test_timed_out_work_is_final_error_and_never_late_forwarded(mode: str) -> None:
    config = settings(
        enforcement_mode=mode,
        sample_rate=1,
        processing_timeout_seconds=0.04,
        classifier_attempt_timeout_seconds=0.02,
    )
    async with appliance(config, SlowClassifier()) as (client, runtime, forwarded):
        response = await client.post("/v1/chat/completions", json=request())
        await runtime._queue.join()
        assert response.status_code == (503 if mode == "block" else 200)
        report = finalized(runtime)
        assert report["classifier_errors"] == report["requests_selected"] == 1
        assert report["requests_classified"] == 0
        assert len(forwarded) == (0 if mode == "block" else 1)


async def test_monitor_queue_exhaustion_is_error_without_delaying_response() -> None:
    config = settings(
        sample_rate=1,
        monitor_queue_limit=1,
        monitor_workers=1,
        processing_timeout_seconds=0.15,
        classifier_attempt_timeout_seconds=0.1,
    )
    async with appliance(config, SlowClassifier()) as (client, runtime, _):
        responses = [await client.post("/v1/chat/completions", json=request()) for _ in range(5)]
        assert all(response.status_code == 200 for response in responses)
        await runtime._queue.join()
        report = finalized(runtime)
        assert (
            report["requests_forwarded"]
            == report["requests_total"]
            == report["requests_selected"]
            == 5
        )
        assert report["classifier_errors"] == 5


async def test_verification_failure_closes_admission_gate() -> None:
    class Unverified(MockClassifier):
        async def classify(self, prompt: str, response: str | None) -> ClassifyResult:
            raise AttestationError(SENTINEL)

    async with appliance(settings(enforcement_mode="block"), Unverified()) as (
        client,
        runtime,
        forwarded,
    ):
        assert (await client.post("/v1/chat/completions", json=request())).status_code == 503
        assert (await client.post("/v1/chat/completions", json=request())).status_code == 503
        assert (await client.get("/aiab/health")).status_code == 503
        assert not forwarded
        assert finalized(runtime)["requests_total"] == 1


def test_ephemeral_tls_material_loads_from_memfd() -> None:
    with ephemeral_tls_files() as (certificate, key):
        assert certificate.startswith("/proc/self/fd/")
        assert key.startswith("/proc/self/fd/")
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(certificate, key)


async def test_startup_verification_failure_never_admits_requests() -> None:
    class StartupFailure(MockClassifier):
        async def verify(self) -> None:
            raise AttestationError(SENTINEL)

    async with appliance(classifier=StartupFailure()) as (client, runtime, forwarded):
        response = await client.post("/v1/chat/completions", json=request())
        assert response.status_code == 503
        assert not forwarded
        assert finalized(runtime)["requests_total"] == 0


async def test_retry_success_and_low_confidence_flag_forward() -> None:
    class RetryClassifier(MockClassifier):
        calls = 0

        async def classify(self, prompt: str, response: str | None) -> ClassifyResult:
            self.calls += 1
            if self.calls == 1:
                raise ClassificationError(SENTINEL)
            return ClassifyResult(True, 0.6)

    async with appliance(settings(enforcement_mode="block"), RetryClassifier()) as (
        client,
        runtime,
        forwarded,
    ):
        assert (await client.post("/v1/chat/completions", json=request())).status_code == 200
        assert len(forwarded) == 1
        report = finalized(runtime)
        assert report["requests_flagged"] == report["requests_classified"] == 1
        assert report["requests_blocked"] == report["classifier_errors"] == 0


@pytest.mark.parametrize(
    "change",
    [
        {"temperature": {"instructions": SENTINEL}},
        {"n": False},
        {"stop": {"text": SENTINEL}},
    ],
)
async def test_nested_generation_data_rejected(change: dict[str, Any]) -> None:
    async with appliance() as (client, runtime, forwarded):
        response = await client.post("/v1/chat/completions", json={**request(), **change})
        assert response.status_code == 400
        assert not forwarded
        assert finalized(runtime)["requests_total"] == 0
