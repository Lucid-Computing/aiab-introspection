"""Reviewed DeepSeek release over attestation-verified TLS; no generic base URL."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

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


REVIEWED_HOST = "deepseek-v4-1-flash-inf16-0.tinfoil.containers.tinfoil.dev"
REVIEWED_TLS_KEY = "bd4fd6b24ad43d34041312694cd9bc6cdf83380829328eb83adf0442601c5488"
BROKER = Path(__file__).resolve().parents[2] / "agent_runtime/verified_transport/broker"
FRAME_LIMIT = 2_097_152


class GoPinnedConnection:
    """Private bounded stdio to the measured broker; no network listener or fallback.

    Credentials travel once over an anonymous pipe, never argv or child environment.
    Cancellation/timeout/invalid protocol permanently poison and reap this broker.
    Each operation independently authenticates the release and key before sending.
    """

    def __init__(self, host, credential):
        if host != REVIEWED_HOST:
            raise RuntimeFailure("unapproved_go_enclave_host")
        self.host, self.credential = host, credential
        self.process = None
        self.failed = False
        self.document = None
        self.lock = asyncio.Lock()

    def check_document(self, doc):
        try:
            if (
                doc["securityVerified"] is not True
                or doc["configRepo"] != MODEL_PROFILE["repo"]
                or doc["enclaveHost"] != self.host
                or "sha256:" + doc["releaseDigest"] != MODEL_PROFILE["release_digest"]
                or doc["tlsPublicKey"] != REVIEWED_TLS_KEY
                or datetime.fromisoformat(doc["freshnessExpiresAt"]) <= datetime.now(UTC)
                or doc["verifier"] != {"name": "tinfoil-go", "version": "0.16.4"}
                or any(
                    doc["steps"][step]["status"] != "success"
                    for step in (
                        "fetchDigest",
                        "verifyCode",
                        "verifyEnclave",
                        "compareMeasurements",
                    )
                )
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise RuntimeFailure("verified_transport_failed") from None
        self.document = doc

    async def exchange(self, frame, timeout):
        async with self.lock:
            try:
                if self.failed or self.process is None or self.process.returncode is not None:
                    raise ValueError
                raw = canonical(frame) + b"\n"
                if len(raw) >= FRAME_LIMIT:
                    raise ValueError
                async with asyncio.timeout(timeout):
                    self.process.stdin.write(raw)
                    await self.process.stdin.drain()
                    line = await self.process.stdout.readline()
                if not line.endswith(b"\n") or len(line) >= FRAME_LIMIT:
                    raise ValueError
                result = json.loads(line)
                if result.get("ok") is not True:
                    raise ValueError
                self.check_document(result["verification"])
                return result
            except BaseException as exc:
                self.failed = True
                await self.close()
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise RuntimeFailure("verified_transport_failed") from None

    async def start(self):
        if self.failed or self.process is not None:
            raise RuntimeFailure("verified_transport_failed")
        try:
            self.process = await asyncio.create_subprocess_exec(
                str(BROKER),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={},
                limit=FRAME_LIMIT,
            )
        except OSError:
            self.failed = True
            raise RuntimeFailure("verified_broker_unavailable") from None
        credential, self.credential = self.credential, ""
        await self.exchange(
            {
                "op": "init",
                "host": self.host,
                "repo": MODEL_PROFILE["repo"],
                "digest": MODEL_PROFILE["release_digest"].removeprefix("sha256:"),
                "key": REVIEWED_TLS_KEY,
                "credential": credential,
            },
            120,
        )

    async def request(self, method, path, body, token=None, timeout=120):
        if method != "POST" or path not in {"/tokenize", "/v1/chat/completions"}:
            self.failed = True
            await self.close()
            raise RuntimeFailure("verified_transport_failed")
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            self.failed = True
            await self.close()
            raise RuntimeFailure("verified_transport_failed") from None
        result = await self.exchange({"op": "request", "path": path, "body": payload}, timeout)
        if type(result.get("status")) is not int or not isinstance(result.get("body"), dict):
            self.failed = True
            await self.close()
            raise RuntimeFailure("verified_transport_failed")
        return result["status"], result["body"]

    async def close(self):
        self.failed = True
        self.credential = ""
        if self.process is not None:
            if self.process.returncode is None:
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass
            await self.process.wait()


class DeepSeekModel:
    """The broker owns the sole credential, fixed sampling, context and identity checks.

    Buffered wire responses are deliberately used: no partial structured findings
    escape before validation; the Pi adapter exposes a normal completed event stream.
    No automatic inference retries, recovery transport, router or provider fallback.
    """

    is_test_model = False

    def __init__(self, *, host=None, credential=None, profile=None, transport=None):
        if profile is not None and profile != MODEL_PROFILE:
            raise RuntimeFailure("unapproved_model_binding")
        self.profile = copy.deepcopy(MODEL_PROFILE)
        self.host = host or os.environ.get("AIAB_DEEPSEEK_HOST", "")
        self.credential = credential or os.environ.get("TINFOIL_API_KEY", "")
        if not self.credential:
            raise RuntimeFailure("missing_tinfoil_inference_credential")
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.tinfoil\.dev", self.host):
            raise RuntimeFailure("missing_reviewed_deepseek_enclave_host")
        self.transport = transport or os.environ.get("AIAB_DEEPSEEK_TRANSPORT", "python")
        if self.transport == "python":
            self.connection = PinnedConnection(
                self.host, self.profile["repo"], self.profile["release_digest"]
            )
        elif self.transport == "go":
            self.connection = GoPinnedConnection(self.host, self.credential)
            self.credential = ""  # the private broker owns it after initialization
        else:
            raise RuntimeFailure("unapproved_model_transport")

    async def start(self):
        await self.connection.start()

    def verification_document(self):
        if isinstance(self.connection, GoPinnedConnection):
            if self.connection.failed or self.connection.document is None:
                raise RuntimeFailure("verified_transport_failed")
            return copy.deepcopy(self.connection.document)
        self.connection.check()
        return self.connection.secure.get_verification_document().to_dict()

    async def count_tokens(self, messages, tools):
        if self.transport == "go":
            from agent_runtime.verified_transport import render_prompt

            # The release's chat tokenizer cannot deepcopy validated tool_calls.
            # Use the exact released encoder, then tokenize its raw prompt remotely.
            payload = {
                "model": MODEL_ID,
                "prompt": render_prompt(messages, tools),
                "add_special_tokens": False,
            }
        else:
            payload = {
                "model": MODEL_ID,
                "messages": messages,
                "tools": tools,
                "add_generation_prompt": True,
                "chat_template_kwargs": MODEL_PROFILE["inference"]["chat_template_kwargs"],
            }
        status, result = await self.connection.request(
            "POST",
            "/tokenize",
            canonical(payload),
            self.credential,
            timeout=30,
        )
        if status == 401 or status == 403:
            raise RuntimeFailure("tinfoil_credential_lacks_inference_access")
        if status != 200 or type(result.get("count")) is not int or result["count"] < 0:
            raise RuntimeFailure("verified_tokenizer_unavailable")
        if result["count"] >= MODEL_PROFILE["context_tokens"]:
            raise RuntimeFailure("model_context_exhausted")
        return result["count"]

    async def complete(self, messages, tools, max_tokens):
        if type(max_tokens) is not int or not 0 < max_tokens <= MODEL_PROFILE["context_tokens"]:
            raise RuntimeFailure("invalid_model_token_limit")
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
        answer = validate_completion(result)
        if answer["usage"]["completion_tokens"] > max_tokens:
            raise RuntimeFailure("provider_usage_exceeds_reservation")
        return answer

    async def close(self):
        await self.connection.close()


def validate_completion(result):
    try:
        if (
            not isinstance(result, dict)
            or result.get("model") != MODEL_ID
            or not isinstance(result.get("choices"), list)
            or len(result["choices"]) != 1
        ):
            raise ValueError
        choice = result["choices"][0]
        if not isinstance(choice, dict) or choice["finish_reason"] not in {"stop", "tool_calls"}:
            raise ValueError
        message = choice["message"]
        if (
            not isinstance(message, dict)
            or message.get("role") != "assistant"
            or message.get("refusal")
            or (
                message.get("reasoning_content") is not None
                and not isinstance(message["reasoning_content"], str)
            )
        ):
            raise ValueError
        if message.get("reasoning") is not None:
            if not isinstance(message["reasoning"], str) or (
                message.get("reasoning_content") is not None
                and message["reasoning_content"] != message["reasoning"]
            ):
                raise ValueError
            message = {**message, "reasoning_content": message["reasoning"]}
            message.pop("reasoning", None)
        usage = result["usage"]
        if not isinstance(usage, dict) or any(
            type(usage[k]) is not int or usage[k] < 0
            for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        ):
            raise ValueError
        if usage["prompt_tokens"] + usage["completion_tokens"] != usage["total_tokens"]:
            raise ValueError
        calls = message.get("tool_calls") or []
        if (
            not isinstance(calls, list)
            or len(calls) > 40
            or len({c["id"] for c in calls}) != len(calls)
        ):
            raise ValueError
        if bool(calls) != (choice["finish_reason"] == "tool_calls"):
            raise ValueError
        for call in calls:
            if (
                call["type"] != "function"
                or not isinstance(call["id"], str)
                or not call["id"]
                or not isinstance(call["function"]["name"], str)
                or not call["function"]["name"]
                or not isinstance(json.loads(call["function"]["arguments"]), dict)
            ):
                raise ValueError
        if not calls and not isinstance(message.get("content"), str):
            raise ValueError
        return {"message": message, "usage": usage}
    except (KeyError, TypeError, ValueError, AttributeError):
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
                message["reasoning"] = thinking
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
