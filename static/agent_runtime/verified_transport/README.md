# Reviewed DeepSeek transport

Opt in with `AIAB_DEEPSEEK_TRANSPORT=go`. The existing Python transport remains
the default. There is no automatic switch or fallback. Only DeepSeek through the
reviewed Tinfoil host is supported. The supervisor starts this binary over bounded
anonymous stdin/stdout pipes; it exposes no HTTP listener or localhost authority.
The Pi process receives neither the credential nor the broker descriptors.

## Linux x86_64 build

Use official Go **1.27.3**, Linux amd64 archive SHA256
`65efbc80fdc1fa2cd2631cad5b481777a68e8725f34e89f50fc6ac714e379b40`.
Install it in your build environment, then:

```sh
make -C static/agent_runtime/verified_transport verify test all GO=/path/to/go1.27.3/bin/go
npm ci --prefix static/agent_runtime --ignore-scripts
make -C static/agent_runtime
uv sync --project static --group dev
uv run --project static pytest -q static/tests/workbench/test_verified_model.py static/tests/workbench/test_runtime.py
```

`go.mod` pins official `github.com/tinfoilsh/tinfoil-go v0.16.4`, upstream commit
`e1381b71ac15f152b2ab9b2d0f2e2a1805e7e777`, with module checksum
`h1:xR7MMH2wA2FxXOQtuA3L20sMhHnBtYOPxmdkG74mZTk=`. `go.sum` pins transitive modules.
There are no replacements, local SDK patches, custom trust roots, policy overrides,
conformance build tags, or ignored freshness checks. Build/install the static
`broker` beside these files **before** measuring the runtime image. Its hash and
all adapter/encoder files must be included in the parent-owned runtime identity.
No compiler, dependency downloader, or executable-path override runs during audits.

## Trust and wire behavior

The broker generates a new nonce and verifies an official v3 attestation before
initial readiness and **every** tokenization/completion. `verifier.New()` default
policy verifies vendor quote/certificates, Sigstore code provenance, signed platform
endorsements, nonce binding, measurements/register policy, endorsed keys and signed
freshness witnesses. Its trusted reference is the fixed repository **and digest**:

- Host: `deepseek-v4-1-flash-inf16-0.tinfoil.containers.tinfoil.dev`
- Repo: `tinfoilsh/confidential-deepseek-v4-1-flash`
- Release: `v0.0.10+b200`, SHA256 `71f41a053232a06dd6ed61ca3a40502a9b972dc16dd9f04ca895ced5a7809def`
- TLS SPKI SHA256: `bd4fd6b24ad43d34041312694cd9bc6cdf83380829328eb83adf0442601c5488`

After verification, these pins and authenticated freshness expiry are checked
independently. Only then is a credential/body attached to a fixed-path POST. The
SDK's raw `TLSBoundRoundTripper` enforces the exact endorsed certificate key during
TLS establishment, before HTTP headers/body. The high-level SDK client, its
recovery transport and OpenAI retries are never used. POST bodies cannot rewind;
redirects, malformed JSON, streaming responses and oversized replies fail closed.
No recovery is attempted; a new explicitly launched run must pass all the same pins.
A legitimate key rotation or release/platform incompatibility stops availability.

The credential is supplied once by the trusted Python supervisor over stdin;
never argv, inherited environment, stdout, Pi, artifacts or logs. The child gets
an empty environment and no unrelated descriptors. Responses are private pipes
back to the supervisor, not public logs. Auth error bodies are discarded. Frames
are bounded to 2 MiB, SDK attestation fetching to 32 MiB/30 seconds, operations to
supervisor deadlines. Timeout/cancellation/protocol failure kills and reaps the
broker and permanently poisons the connection. This is not memory-zeroization or
an independent confidential-computing boundary: supervisor and broker belong in
the same measured, trusted enclave. The implementation does not alter parent
container, approval, disclosure, service, sandbox or deployment code.

## Exact remote tokenization

The pinned serving release's chat `/tokenize` route returns HTTP 400 for tool-call
histories: `cannot pickle 'pydantic_core._pydantic_core.ValidatorIterator' object`.
The Go-selected adapter therefore always renders the prompt with the **exact
released encoder**, then sends `{model,prompt,add_special_tokens:false}` to the same
verified `/tokenize` route. Token counts still come from the enclave tokenizer;
there is no local estimator or silent fallback after a failed chat request.

`deepseek_v41_encoding.py` is vLLM commit
`db9527a46873454610df6dbedf79a36d6bf1a7f6`, file
`vllm/tokenizers/deepseek_v41_encoding.py`, with only the measured serving release's
`patches/0009-released-reasoning.patch` (low=25/high=50) applied. SHA256:
`081382cb953a1c8041fdfc82a6001cf0c932553171436625c89fe5b9fbcda464`.
The text-only wrapper follows upstream normalization and tool insertion. Upstream
Apache-2.0 notices and license are retained. No model-generated code is loaded.
Current vLLM's `reasoning` wire field is normalized privately and preserved on tool
rounds; conflicting aliases, truncation and usage outside reservation fail closed.

## Live qualification

From the repository root, with a read-only operator env file already containing
`TINFOIL_API_KEY` (its value is never printed):

```sh
uv run --project static python static/agent_runtime/qualify.py \
  --transport go \
  --host deepseek-v4-1-flash-inf16-0.tinfoil.containers.tinfoil.dev \
  --env-file /operator/read-only/.env \
  --output static/agent_runtime/review/live-qualification.json
```

The command verifies chat/raw tokenizer parity, then runs real multi-turn Pi with
real DeepSeek against five projected events from Northstar seed 17, using real
contracts/evidence, all seven tools and kernel-isolated SQL/Python. It validates
returned findings/citations. No truth ledger, real personal data, reasoning text,
credential, tool payload or private transcript is printed/persisted. Reports record
public attestation identities, counts and safe status/error codes. Failure reports
remain failures; unit doubles do not count as live inference. See
[the transport review](../review/VERIFIED_TRANSPORT_REVIEW.md) for actual outcomes.
