"""A bounded, text-only OpenAI-to-Anthropic adapter; credentials never leave this hop."""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from aiab.classifier import serialize_messages, strict_json_loads
from aiab.config import ANTHROPIC_ORIGIN, Config

MAX_OUTPUT_TOKENS = 2048
MAX_RESPONSE_BYTES = 1_048_576
_FIELDS = {
    "model",
    "messages",
    "stream",
    "temperature",
    "top_p",
    "n",
    "max_tokens",
    "max_completion_tokens",
    "stop",
}


def prepare_request(body: dict[str, Any], config: Config) -> dict[str, Any]:
    """Validate before admission/outbound work; never silently reinterpret unsupported input."""
    if set(body) - _FIELDS or body.get("model") != config.anthropic_model:
        raise ValueError("Unsupported Anthropic request or model")
    if type(body.get("stream", False)) is not bool or body.get("stream", False):
        raise ValueError("Streaming is unsupported")
    if type(body.get("n", 1)) is not int or body.get("n", 1) != 1:
        raise ValueError("Only one completion is supported")
    if "max_tokens" in body and "max_completion_tokens" in body:
        raise ValueError("Specify only one output limit")
    limit = body.get("max_tokens", body.get("max_completion_tokens", 512))
    if type(limit) is not int or not 1 <= limit <= MAX_OUTPUT_TOKENS:
        raise ValueError("Output limit is outside the supported range")
    serialize_messages(body.get("messages"))
    messages: list[dict[str, Any]] = []
    system: list[dict[str, str]] = []
    for message in body["messages"]:
        role = message["role"]
        content = message["content"]
        parts = [{"type": "text", "text": content}] if isinstance(content, str) else content
        if any(not part["text"].strip() for part in parts):
            raise ValueError("Empty text blocks are unsupported")
        if role == "system":
            if messages:
                raise ValueError("System messages must precede the conversation")
            system.extend(parts)
        elif role in {"user", "assistant"}:
            messages.append({"role": role, "content": parts})
        else:
            # Mapping developer to system would silently change instruction priority.
            raise ValueError("Only leading system, user and assistant roles are supported")
    if not messages or messages[0]["role"] != "user" or messages[-1]["role"] != "user":
        raise ValueError("The conversation must begin and end with user messages")
    result: dict[str, Any] = {
        "model": config.anthropic_model,
        "messages": messages,
        "max_tokens": limit,
        "stream": False,
    }
    if system:
        result["system"] = system
    if "temperature" in body and "top_p" in body:
        raise ValueError("Specify only one sampling option")
    for name in ("temperature", "top_p"):
        if name in body:
            value = body[name]
            if type(value) not in {int, float} or not 0 <= value <= 1:
                raise ValueError("Sampling option is outside the supported range")
            result[name] = value
    if "stop" in body:
        stops = [body["stop"]] if isinstance(body["stop"], str) else body["stop"]
        if (
            not isinstance(stops, list)
            or not 1 <= len(stops) <= 4
            or any(not isinstance(value, str) or not value or len(value) > 256 for value in stops)
        ):
            raise ValueError("Unsupported stop sequences")
        result["stop_sequences"] = stops
    return result


def _failure(status: int) -> httpx.Response:
    if status == 429:
        code, kind, message = (
            "upstream_rate_limited",
            "rate_limit_error",
            "Inference capacity exceeded",
        )
    elif status in {408, 504}:
        status = 504
        code, kind, message = "upstream_timeout", "server_error", "Inference request timed out"
    elif status in {503, 529}:
        status = 503
        code, kind, message = (
            "upstream_unavailable",
            "server_error",
            "Inference service unavailable",
        )
    else:
        status = 502
        code, kind, message = (
            "upstream_unavailable",
            "server_error",
            "Inference service unavailable",
        )
    return httpx.Response(status, json={"error": {"type": kind, "code": code, "message": message}})


def _success(body: bytes, config: Config) -> httpx.Response:
    decoded = strict_json_loads(body)
    if not isinstance(decoded, dict) or (
        decoded.get("type") != "message"
        or decoded.get("role") != "assistant"
        or decoded.get("model") != config.anthropic_model
    ):
        raise ValueError("Invalid upstream response")
    content = decoded.get("content")
    if (
        not isinstance(content, list)
        or not content
        or any(
            not isinstance(part, dict)
            or part.get("type") != "text"
            or not isinstance(part.get("text"), str)
            for part in content
        )
    ):
        raise ValueError("Unsupported upstream content")
    text = "".join(part["text"] for part in content)
    if config.anthropic_api_key in text:
        raise ValueError("Invalid upstream content")
    finish = {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "max_tokens": "length",
        "refusal": "content_filter",
    }.get(decoded.get("stop_reason"))
    if finish is None:
        raise ValueError("Unsupported upstream stop reason")
    usage = decoded.get("usage")
    if not isinstance(usage, dict):
        raise ValueError("Missing upstream usage")
    counts = {}
    for name in (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ):
        value = usage.get(name)
        if name.startswith("cache_") and value is None:
            value = 0
        if type(value) is not int or not 0 <= value <= 1_000_000_000:
            raise ValueError("Invalid upstream usage")
        counts[name] = value
    prompt_tokens = (
        counts["input_tokens"]
        + counts["cache_creation_input_tokens"]
        + counts["cache_read_input_tokens"]
    )
    output_tokens = counts["output_tokens"]
    return httpx.Response(
        200,
        json={
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": config.anthropic_model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": finish,
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": prompt_tokens + output_tokens,
            },
        },
    )


async def complete(
    client: httpx.AsyncClient, config: Config, body: dict[str, Any]
) -> httpx.Response:
    """No redirects, retries, forwarded browser headers, or upstream error/body reflection."""
    try:
        async with client.stream(
            "POST",
            ANTHROPIC_ORIGIN + "/v1/messages",
            json=body,
            headers={
                "x-api-key": config.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "accept-encoding": "identity",
                "content-type": "application/json",
            },
            follow_redirects=False,
            timeout=config.upstream_timeout_seconds,
        ) as upstream:
            if upstream.status_code != 200:
                return _failure(upstream.status_code)
            if upstream.headers.get("content-encoding", "identity") != "identity":
                return _failure(502)
            length = upstream.headers.get("content-length")
            if length is not None and (not length.isdecimal() or int(length) > MAX_RESPONSE_BYTES):
                return _failure(502)
            response = bytearray()
            # Real streamed responses remain undecoded, preventing decompression bombs.
            # Mock transports may supply an already-read response for unit tests.
            if upstream.is_stream_consumed:
                response.extend(upstream.content)
            else:
                async for chunk in upstream.aiter_raw():
                    if len(response) + len(chunk) > MAX_RESPONSE_BYTES:
                        return _failure(502)
                    response.extend(chunk)
            if len(response) > MAX_RESPONSE_BYTES:
                return _failure(502)
            return _success(bytes(response), config)
    except httpx.TimeoutException:
        return _failure(504)
    except Exception:
        return _failure(502)
