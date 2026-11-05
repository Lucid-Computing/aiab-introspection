"""Reviewed DeepSeek release over attestation-verified TLS; no generic base URL."""

from __future__ import annotations

import json
import os
import re

from aiab.audit.protocol import canonical
from aiab.audit.transport import PinnedConnection
from aiab.workbench.tools import RuntimeFailure

MODEL_ID = "deepseek-v4-1-flash"
MODEL_PROFILE = {
    "id": MODEL_ID,
    "repo": "tinfoilsh/confidential-deepseek-v4-1-flash",
    "release_digest": "sha256:71f41a053232a06dd6ed61ca3a40502a9b972dc16dd9f04ca895ced5a7809def",
    "artifact_digest": "sha256:6c4a2b637f7f45d9e48d1bdc38c12e0454584f48d3759851f7ee4471cd90b2e7",
    "model_revision": (
        "deepseek-ai/DeepSeek-V4.1-Flash-1017@1bae0e1088993b9b82de08f59de5be5a34e42170"
    ),
    "context_tokens": 1048576,
    "serving_image": "sha256:b98ca8cc6ace7ecc00eb08b5e550992175f46766a69a68508ca8227490101af5",
    "tool_parser": "deepseek_v41",
    "reasoning_parser": "deepseek_v41",
    "inference": {
        "temperature": 1.0,
        "top_p": 1.0,
        "reasoning_effort": "low",
        "chat_template_kwargs": {
            "enable_thinking": True,
            "reasoning_effort": "low",
            "drop_thinking": False,
        },
    },
}


class DeepSeekModel:
    """The broker owns the sole credential, fixed sampling, context and identity checks.

    Buffered wire responses are deliberately used: no partial structured findings
    escape before validation; the Pi adapter exposes a normal completed event stream.
    No automatic inference retries, recovery transport, router or provider fallback.
    """

    is_test_model = False

    def __init__(self, *, host=None, credential=None, profile=None):
        if profile is not None and profile != MODEL_PROFILE:
            raise RuntimeFailure("unapproved_model_binding")
        self.profile = dict(MODEL_PROFILE)
        self.host = host or os.environ.get("AIAB_DEEPSEEK_HOST", "")
        self.credential = credential or os.environ.get("TINFOIL_API_KEY", "")
        if not self.credential:
            raise RuntimeFailure("missing_tinfoil_inference_credential")
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.tinfoil\.dev", self.host):
            raise RuntimeFailure("missing_reviewed_deepseek_enclave_host")
        self.connection = PinnedConnection(
            self.host, self.profile["repo"], self.profile["release_digest"]
        )

    async def start(self):
        await self.connection.start()

    def verification_document(self):
        self.connection.check()
        return self.connection.secure.get_verification_document().to_dict()

    async def count_tokens(self, messages, tools):
        status, result = await self.connection.request(
            "POST",
            "/tokenize",
            canonical(
                {
                    "model": MODEL_ID,
                    "messages": messages,
                    "tools": tools,
                    "add_generation_prompt": True,
                    "chat_template_kwargs": MODEL_PROFILE["inference"]["chat_template_kwargs"],
                }
            ),
            self.credential,
            timeout=30,
        )
        if status == 401 or status == 403:
            raise RuntimeFailure("tinfoil_credential_lacks_inference_access")
        if status != 200 or type(result.get("count")) is not int or result["count"] < 0:
            raise RuntimeFailure("verified_tokenizer_unavailable")
        return result["count"]

    async def complete(self, messages, tools, max_tokens):
        request = {
            "model": MODEL_ID,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 1.0,
            "top_p": 1.0,
            "reasoning_effort": "low",
            "chat_template_kwargs": MODEL_PROFILE["inference"]["chat_template_kwargs"],
            "max_tokens": max_tokens,
            "stream": False,
        }
        status, result = await self.connection.request(
            "POST", "/v1/chat/completions", canonical(request), self.credential, timeout=120
        )
        if status in (401, 403):
            raise RuntimeFailure("tinfoil_credential_lacks_inference_access")
        if status != 200:
            raise RuntimeFailure("deepseek_inference_unavailable")
        return validate_completion(result)

    async def close(self):
        await self.connection.close()


def validate_completion(result):
    try:
        if result.get("model") != MODEL_ID or len(result["choices"]) != 1:
            raise ValueError
        choice = result["choices"][0]
        if choice["finish_reason"] not in {"stop", "tool_calls"}:
            raise ValueError
        message = choice["message"]
        if message.get("role") != "assistant" or message.get("refusal"):
            raise ValueError
        usage = result["usage"]
        if any(
            type(usage[k]) is not int or usage[k] < 0
            for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        ):
            raise ValueError
        if usage["prompt_tokens"] + usage["completion_tokens"] != usage["total_tokens"]:
            raise ValueError
        calls = message.get("tool_calls") or []
        if len(calls) > 40 or len({c["id"] for c in calls}) != len(calls):
            raise ValueError
        if bool(calls) != (choice["finish_reason"] == "tool_calls"):
            raise ValueError
        for call in calls:
            if (
                call["type"] != "function"
                or not isinstance(call["id"], str)
                or not isinstance(json.loads(call["function"]["arguments"]), dict)
            ):
                raise ValueError
        if not calls and not isinstance(message.get("content"), str):
            raise ValueError
        return {"message": message, "usage": usage}
    except (KeyError, TypeError, ValueError):
        raise RuntimeFailure("invalid_deepseek_completion") from None


def wire_messages(pi_messages, system):
    """Preserve DeepSeek reasoning on tool rounds, privately; reject other modalities.

    System text and tool declarations come from the broker's frozen plan. Pi's
    transcript cannot change the approved system, tools, provider or model.
    """
    result = [{"role": "system", "content": system}]
    for item in pi_messages:
        role = item["role"]
        if role == "system":
            continue
        if role == "toolResult":
            result.append(
                {
                    "role": "tool",
                    "tool_call_id": item["toolCallId"],
                    "content": text_content(item["content"]),
                }
            )
        elif role == "user":
            result.append({"role": "user", "content": text_content(item["content"])})
        elif role == "assistant":
            blocks = item["content"]
            message = {
                "role": "assistant",
                "content": "".join(b["text"] for b in blocks if b["type"] == "text") or None,
            }
            thinking = "".join(b["thinking"] for b in blocks if b["type"] == "thinking")
            if thinking:
                message["reasoning_content"] = thinking
            calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": json.dumps(b["arguments"])},
                }
                for b in blocks
                if b["type"] == "toolCall"
            ]
            if calls:
                message["tool_calls"] = calls
            result.append(message)
        else:
            raise RuntimeFailure("invalid_pi_message")
    return result


def text_content(value):
    if isinstance(value, str):
        return value
    if not isinstance(value, list) or any(b.get("type") != "text" for b in value):
        raise RuntimeFailure("unsupported_modality")
    return "".join(b["text"] for b in value)


def pi_completion(answer):
    message, usage = answer["message"], answer["usage"]
    blocks = []
    if message.get("reasoning_content"):
        blocks.append({"type": "thinking", "thinking": message["reasoning_content"]})
    if message.get("content"):
        blocks.append({"type": "text", "text": message["content"]})
    for call in message.get("tool_calls") or []:
        blocks.append(
            {
                "type": "toolCall",
                "id": call["id"],
                "name": call["function"]["name"],
                "arguments": json.loads(call["function"]["arguments"]),
            }
        )
    return {
        "content": blocks,
        "stopReason": "toolUse" if message.get("tool_calls") else "stop",
        "usage": {
            "input": usage["prompt_tokens"],
            "output": usage["completion_tokens"],
            "totalTokens": usage["total_tokens"],
            "cacheRead": 0,
            "cacheWrite": 0,
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0},
        },
    }
