"""Synthetic generator boundaries, accounting, and corpus validation without network access."""

import argparse
import json
import random
from collections import Counter
from pathlib import Path

import httpx
import pytest

from traffic.generate import (
    Prompt,
    Results,
    generate,
    load_prompts,
    model_id,
    policy_settings,
    positive_count,
    positive_rate,
    response_outcome,
    select_prompts,
    summarize,
    validate_url,
)


def test_corpus_balance_and_challenge_reservation() -> None:
    prompts = load_prompts()
    assert Counter(prompt.label for prompt in prompts) == {"benign": 60, "capabilities": 60}
    assert len({prompt.prompt for prompt in prompts}) == 120
    challenges = [prompt for prompt in prompts if prompt.split == "held_out"]
    assert len(challenges) >= 12
    assert {prompt.label for prompt in challenges} == {"benign", "capabilities"}
    categories = {prompt.category for prompt in challenges}
    assert {"ml_coursework", "policy_discussion", "indirect_capabilities"} <= categories
    assert "classifier_instruction_injection" in categories
    for pair in ("config", "examples"):
        assert f"paired_ambiguity_{pair}_with_context" in categories
        assert f"paired_ambiguity_{pair}_without_context" in categories


@pytest.mark.parametrize(
    "record",
    [
        "secret contents invalid JSON",
        "[]",
        '{"prompt": "secret contents", "label": "unknown"}',
        '{"prompt": "", "label": "benign"}',
        '{"prompt": "secret contents", "label": "benign", "split": "train"}',
        '{"prompt": "secret contents", "label": "benign", "category": []}',
        '{"prompt": "secret contents", "label": "benign", "extra": true}',
    ],
)
def test_invalid_corpus_is_not_reflected(tmp_path: Path, record: str) -> None:
    corpus = tmp_path / "prompts.jsonl"
    corpus.write_text(record)
    with pytest.raises(ValueError, match="Invalid corpus record at line 1") as failure:
        load_prompts((corpus,))
    assert "secret contents" not in str(failure.value)


def test_empty_corpus_rejected(tmp_path: Path) -> None:
    corpus = tmp_path / "empty.jsonl"
    corpus.write_text("")
    with pytest.raises(ValueError, match="empty"):
        load_prompts((corpus,))


def test_selection_shuffles_and_covers_complete_cycles() -> None:
    prompts = load_prompts()
    selection = select_prompts(prompts, 250, random.Random(73))
    assert len(selection) == 250
    assert set(selection[:120]) == set(prompts)
    assert set(selection[120:240]) == set(prompts)
    assert selection[:120] != prompts
    assert selection[:120] != selection[120:240]


@pytest.mark.parametrize("value", ["0", "-1", "abc", "1.5"])
def test_invalid_count(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        positive_count(value)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "abc"])
def test_invalid_rate(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        positive_rate(value)


@pytest.mark.parametrize(
    "url,insecure",
    [
        ("https://example.com", True),
        ("https://localhost.example.com", True),
        ("http://localhost:8443", True),
        ("http://127.0.0.1:8443", True),
        ("http://[::1]:8443", True),
        ("https://username:password@localhost", False),
        ("https://localhost/v1", False),
        ("https://localhost?token=secret", False),
        ("file:///tmp/secret", False),
        ("https://localhost:invalid", False),
    ],
)
def test_endpoint_restrictions(url: str, insecure: bool) -> None:
    with pytest.raises(ValueError):
        validate_url(url, insecure)


def test_loopback_insecure_is_explicit() -> None:
    assert validate_url("https://localhost:8443/", True) == "https://localhost:8443"
    assert validate_url("https://[::1]:8443", True) == "https://[::1]:8443"
    assert validate_url("https://example.com", False) == "https://example.com"
    assert validate_url("http://localhost:8080", False) == "http://localhost:8080"


def test_mock_http_discovery_request_boundary_and_accounting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("traffic.generate.time.sleep", lambda _: None)
    sent: list[dict[str, object]] = []
    prompts = [
        Prompt("PRIVATE PROMPT 1", "benign", "category-1", "held_out"),
        Prompt("PRIVATE PROMPT 2", "capabilities", "category-2", "held_out"),
        Prompt("PRIVATE PROMPT 3", "capabilities", "category-3", "held_out"),
        Prompt("PRIVATE PROMPT 4", "benign"),
        Prompt("PRIVATE PROMPT 5", "benign"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "served-model"}]})
        if request.url.path == "/aiab/manifest":
            return httpx.Response(200, json={"enforcement_mode": "block", "sample_rate": 0.1})
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        sent.append(body)
        assert set(body) == {"model", "messages", "stream", "max_tokens"}
        assert body["model"] == "served-model"
        assert body["stream"] is False
        assert body["max_tokens"] == 32
        assert body["messages"] == [{"role": "user", "content": prompts[len(sent) - 1].prompt}]
        assert all(token not in request.headers for token in ("label", "category", "split"))
        match len(sent):
            case 1:
                return httpx.Response(200, json={"content": "PRIVATE RESPONSE"})
            case 2:
                return httpx.Response(403, json={"error": {"code": "aiab_blocked"}})
            case 3:
                return httpx.Response(503, json={"error": {"code": "classifier_unavailable"}})
            case 4:
                return httpx.Response(503, json={"error": {"code": "upstream_busy"}})
            case _:
                raise httpx.ConnectError("PRIVATE TRANSPORT ERROR", request=request)

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://localhost"
    ) as client:
        model = model_id(client)
        mode, rate = policy_settings(client)
        results = generate(client, prompts, model, 10)
    assert results.outcomes == {
        "success": 1,
        "policy_block": 1,
        "classifier_error": 1,
        "http_error": 1,
        "transport_error": 1,
    }
    assert results.by_label["capabilities"] == {"policy_block": 1, "classifier_error": 1}
    assert results.held_out["benign"] == {"success": 1}
    summary = summarize(results, mode, rate)
    assert "Held-out label comparison" in summary
    assert "classifier failures (503): 1" in summary
    assert "not benign predictions" in summary
    assert "PRIVATE" not in summary
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_unrecognized_errors_are_not_policy_decisions() -> None:
    assert response_outcome(httpx.Response(403, text="not a policy block")) == "http_error"
    assert response_outcome(httpx.Response(503, json={"error": []})) == "http_error"
    assert response_outcome(httpx.Response(503, json=[])) == "http_error"


def test_unavailable_metadata_and_models_fail_without_response_contents() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, text="PRIVATE INVALID RESPONSE"))
    with httpx.Client(transport=transport, base_url="https://localhost") as client:
        assert policy_settings(client) == ("unknown", None)
        with pytest.raises(ValueError, match="Could not obtain a model") as failure:
            model_id(client)
        assert "PRIVATE" not in str(failure.value)


def test_monitor_estimate_is_not_a_flag_estimate() -> None:
    results = Results()
    results.record(Prompt("one", "benign"), "success")
    results.record(Prompt("two", "capabilities"), "success")
    summary = summarize(results, "monitor", 0.25)
    assert "0.5000 × 0.25 × 2 = 0.25" in summary
    assert "If all attempts were admitted" in summary
    assert "not classifier flags" in summary
    assert "ambiguous" in summary


def test_generator_enforces_spacing(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [100.0]
    starts: list[float] = []

    def sleep(duration: float) -> None:
        clock[0] += duration

    def handler(_: httpx.Request) -> httpx.Response:
        starts.append(clock[0])
        clock[0] += 0.1
        return httpx.Response(200)

    monkeypatch.setattr("traffic.generate.time.sleep", sleep)
    monkeypatch.setattr("traffic.generate.time.monotonic", lambda: clock[0])
    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://localhost"
    ) as client:
        generate(client, [Prompt("test", "benign")] * 3, "model", 2)
    assert starts == [100.0, 100.5, 101.0]
