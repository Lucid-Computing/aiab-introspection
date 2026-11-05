"""Exact text-only prompt renderer for the reviewed DeepSeek serving release.

The encoder is upstream vLLM db9527a46873454610df6dbedf79a36d6bf1a7f6,
with the serving release's 0009-released-reasoning.patch (low=25, high=50).
This renders a prompt; it NEVER estimates token counts. Tokenization happens only
on the verified enclave. Tool messages use the same encoder as inference.
"""

import json

from aiab.audit.protocol import canonical

from .deepseek_v41_encoding import encode_messages


def render_prompt(messages, tools):
    # Inference requests use RFC8785. Preserve the key order the remote encoder
    # actually receives, especially JSON schemas embedded in its tools prompt.
    conversation = json.loads(canonical(messages))
    for message in conversation:
        if message.get("role") not in {"system", "user", "assistant", "tool"}:
            raise ValueError("unsupported_prompt_role")
        if message.get("content") is not None and not isinstance(message["content"], str):
            raise ValueError("unsupported_prompt_modality")
        # Current vLLM's wire field is reasoning. The encoder's internal field is
        # reasoning_content; this is the exact upstream normalization.
        if "reasoning" in message:
            message["reasoning_content"] = message["reasoning"]
    if tools:
        system = next((m for m in conversation if m["role"] == "system"), None)
        if system is None:
            system = {"role": "system", "content": ""}
            conversation.insert(0, system)
        system["tools"] = json.loads(canonical(tools))
    return encode_messages(
        conversation, thinking_mode="thinking", drop_thinking=False, reasoning_effort="low"
    )
