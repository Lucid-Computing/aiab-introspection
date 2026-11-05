# DeepSeek serving binding review — 2026-11-05

This is implementation-source review, not third-party security approval or evidence
that live inference succeeded. Existing GPTOSS bindings were neither reused nor
modified. Candidate: `tinfoilsh/confidential-deepseek-v4-1-flash`, **v0.0.10+b200**.

Primary sources inspected:

- [Published release](https://github.com/tinfoilsh/confidential-deepseek-v4-1-flash/releases/tag/v0.0.10%2Bb200),
  including actual `tinfoil-deployment.json` and `tinfoil.hash` assets.
- [Tagged measured config](https://github.com/tinfoilsh/confidential-deepseek-v4-1-flash/blob/v0.0.10%2Bb200/tinfoil-config.yml)
  and [B200 Dockerfile](https://github.com/tinfoilsh/confidential-deepseek-v4-1-flash/blob/v0.0.10%2Bb200/Dockerfile.b200).
- Tagged `scripts/b200_finalist.py`, `scripts/b200_runtime.py`,
  `scripts/b200_bundle.py`, `scripts/b200-entrypoint.sh`, patch inventory,
  `patches/0009-released-reasoning.patch` and B200 patch review guide.
- [Official Pi core source/API](https://github.com/badlogic/pi-mono/tree/main/packages/agent)
  and the actual npm-published `@earendil-works/pi-agent-core`, `pi-ai` and `pi-schema`
  1.5.0 source and typings installed from the locked registry artifacts.
- [Official Tinfoil Python SDK](https://github.com/tinfoilsh/tinfoil-python), including
  the installed 0.14.0 TLS pinning, origin restrictions, verification steps, recovery
  hooks and TDX policy. The separately installed current 0.15.0 has the same relevant
  MR_SEAM allowlist. No verifier policy was patched.

The SHA256 of the **actual deployment JSON bytes** is
`71f41a053232a06dd6ed61ca3a40502a9b972dc16dd9f04ca895ced5a7809def`, matching
`tinfoil.hash`. Its decoded config hash is
`0502f00cfa97e5d8c7a78fae5365b715bce144c6585a865826e3516fabf144b7`, bound in the
measured VM command line. The real image digest is
`b98ca8cc6ace7ecc00eb08b5e550992175f46766a69a68508ca8227490101af5`.
The unbuilt branch/B200 template has an all-zero placeholder image: it is not the
binding. Only the published deployment/config is accepted by this runtime profile.

The config names Hugging Face revision
`deepseek-ai/DeepSeek-V4.1-Flash-1017@1bae0e1088993b9b82de08f59de5be5a34e42170`
and schema-2 model pack root
`6c4a2b637f7f45d9e48d1bdc38c12e0454584f48d3759851f7ee4471cd90b2e7`.
The serving command loads the corresponding `/tinfoil/mpk/mpk-...` path, not a
mutable model-name download. The root is a model-pack commitment, not an assertion
that the API model name itself is a weight hash or that the weights were downloaded
and independently recomputed here. The measured CVM/model-pack loader remains part
of the trusted implementation; attestation must bind that loader/config/image.

The B200 Dockerfile pins vLLM 0.31.0 base image and source commit
`db9527a46873454610df6dbedf79a36d6bf1a7f6`, checks patch application, hashes compiled
kernel artifacts and source compatibility, and invokes the finalist loader before
vLLM serve. The loader enforces the selected model-pack path, normal CC mode,
configuration, sources, compiled caches, and GPU configuration. This review did not
rebuild the GPU image or independently execute its kernels.

The measured CLI selects `deepseek_v41` tokenizer, tool-call parser and reasoning
parser, enables automatic tool choice and usage, and serves `deepseek-v4-1-flash`
with 1,048,576 context tokens. The released reasoning patch changes low effort to
25 and high to 50; the broker explicitly requests low effort. Sampling is fixed
at temperature/top_p 1.0. The adapter preserves private `reasoning_content` through
tool turns, function-call IDs/JSON arguments, tool-result roles, and token usage;
truncation, malformed/mismatched responses and unsupported modalities fail.
Wire responses are buffered, not token-streamed. No semantic context compaction
or hidden summarization provider is installed. Context overflow fails explicitly.

The broker reuses the reviewed attested-TLS transport with exact repo/release and
key consistency checks, before bodies are sent. Recovery transports raise instead
of changing identity. Direct host discovery from public router metadata is only a
hint: its advertised model/tag does not establish trust. The SDK independently
verifies hardware, code measurement and TLS key, then the runtime compares the
reviewed release digest. SDK latest-release selection is followed by an exact pin
check; a newer latest release may cause an availability failure, never repinning.
No unrestricted OpenAI-compatible transport is used.

## Actual live observations

The read-only operator `.env` had a Tinfoil credential; its value was never printed,
tracked or passed into Node. Public model listing included the candidate. The
control-plane router-config endpoint returned HTTP 403; this does not determine
inference entitlement. No control-plane changes were attempted.

Direct B200 hosts inf15-0 and inf15-1 timed out. inf16-0 returned its attestation,
but SDK verification rejected:

`MR_SEAM d0d80c085166ba78ccc69af268e5753cf0f3394523cb4ff7c50b08d9265c82489c099c377be6a400e4d2b57da924012c does not match any allowed value`

Both the repository-pinned 0.14.0 and separately installed official 0.15.0 failed
before credentialed tokenization or inference. Thus no inference permission failure
or live tool/reasoning behavior can be claimed. `live-qualification.json` records
zero turns, tokens and tools. The failure is a serving-hardware/verifier compatibility
gate, not justification to skip verification or use the separately advertised B300
release, which this profile has not admitted.

The source review also followed the pinned upstream vLLM commit into
`vllm/parser/deepseek_v41.py`, `parser/engine/registered_adapters.py`,
`tool_parsers/deepseekv41_engine_tool_parser.py`,
`reasoning/deepseek_v41_engine_reasoning_parser.py`, and
`tokenizers/deepseek_v41{,_encoding}.py`. The V4.1 parser uses spaced DSML tool
blocks and inherits the V4 argument converter; the HTTP API exposes normalized
function calls rather than requiring the broker to parse raw DSML. Its tokenizer
accepts `reasoning_content`, merges tool results into ordered user-side blocks by
call ID, and has explicit thinking/drop_thinking switches. Tokenization and
inference now supply identical low-effort/enable_thinking/drop_thinking=False
chat-template kwargs, avoiding a tokenizer count using the default high effort.
These inference settings are included in the final signed profile. The retained final qualification artifact never passed attestation or sent an
inference request. A local consistency test also checks the profile image against
the actual decoded deployment bytes, preventing transcription errors in the binding.
