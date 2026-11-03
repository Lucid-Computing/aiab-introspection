# AIAB Demo — Implementation Plan

This file is the instruction set for setting up the repository. Read it fully before writing code.

## 1. Goal

Build a working demo of privacy-preserving inference introspection.

An appliance (the **AIAB**, "AI in a box") sits in front of an AI inference server. It runs inside a secure enclave (TEE). It:

1. Terminates TLS and forwards requests to the inference server.
2. Randomly samples a fraction of request/response pairs (monitor mode), or classifies every request before forwarding (block mode).
3. Classifies with an LLM that also runs inside an attested enclave.
4. Emits a small, fixed-schema, signed report per period: requests seen, selected for classification, successfully classified, flagged, forwarded, and blocked, plus classification failures.

An **auditor** verifies that the AIAB runs the published code with the expected policy configuration, fetches reports over an attested channel, checks signatures, and prints a rollup such as: "3 of 25 successfully classified requests sampled at this proxy were flagged as AI capabilities research; 0 classification failures."

The final demo is a **web chat interface** that talks to the inference server through the AIAB in block mode. A benign prompt gets a normal answer. An obviously ML-training-related prompt is rejected with a visible "Blocked by AIAB" message. The auditor rollup then shows the block count.

The classifier definition is a pluggable text file. The base implementation targets "AI research / capabilities-increasing activity." Other definitions (for example, dangerous biology) plug in the same way.

### Verification claim and limits

The demo provides attestable measurement of policy-relevant inference **received by this proxy**, under the TEE security assumptions. It does not establish datacenter-wide coverage, absence of prohibited activity, or the fraction of compute spent on AI research. Internal agent activity and traffic sent to other endpoints are outside its observation boundary. A network uplink alone need not see internal inference.

The privacy goal is to avoid disclosing conversation contents to the auditor or inspection infrastructure outside the enclaves. The governed inference server still receives forwarded conversations. Exact aggregate counts, especially for small periods, can reveal information; version 1 does not promise differential privacy or protection against all covert channels. Use synthetic traffic for the demo.

Blocking the preset prompt illustrates policy enforcement at this endpoint. It does not establish robust detection of adversarial or distributed AI research. Keep these limits visible in the README and demo narration.

## 2. Scope for this version

Build the base design only. Do **not** build the items in section 10 (Deferred). Leave clean interfaces so they can be added later.

In scope:

- Local end-to-end run on a laptop (Milestone 1).
- Real classifier via the Tinfoil inference API (Milestone 2).
- AIAB deployed as a Tinfoil Container with attestation verified by the auditor (Milestone 3).
- Adversarial demo script (Milestone 4).
- Web chat demo with block mode and two preset prompts (Milestone 5).
- Small additions within existing components: a policy manifest, boot/sequence identifiers, an optional auditor checkpoint, and challenge cases in the existing evaluation set. No additional service is required.

Out of scope: everything in section 10.

## 3. Platform decisions

- **TEE provider:** Tinfoil (https://tinfoil.sh). The AIAB runs as a Tinfoil Container. The classifier is called through the Tinfoil inference API using the Tinfoil SDK, which verifies the inference enclave's attestation on every connection.
- **Language:** Python 3.12. Use `uv` for dependency management with a committed lockfile.
- **Governed inference server:** any OpenAI-compatible server running a small open instruct model. Default: `llama.cpp` server (`llama-server`) with a 1B–3B GGUF model. Runs on a plain machine, not in an enclave. It plays the role of "the operator's model."
- **Web chat:** one static HTML file with inline CSS and JavaScript. No framework, no build step. Served by a pinned `nginx:alpine` container. It plays the role of "the operator's product."
- **Container:** one Dockerfile for the AIAB with a pinned base image digest and pinned dependencies. The build must be reproducible: same source → same image digest.
- **Config:** environment variables only. No config files with secrets. Document every variable in `README.md`.
- Parse configuration once at startup and freeze it for that boot. Load the classifier definition once from the image; no runtime edits, remote policy downloads, or mounted policy overrides in the deployed demo. Section 5.4 defines the non-secret manifest derived from effective configuration.

**Before writing any Tinfoil-related code, read the Tinfoil docs at https://docs.tinfoil.sh.** Specifically: the Python SDK, Tinfoil Containers deployment, how TLS reaches the app inside a container, outbound network rules, and which inference models support structured/JSON output. Do not guess the SDK API. If a doc is unclear, write down the question in `docs/OPEN_QUESTIONS.md` and pick the simplest safe assumption.

## 4. Repository layout

```
aiab-demo/
  README.md
  Makefile
  pyproject.toml
  uv.lock
  aiab/                  # the appliance
    __init__.py
    main.py              # entrypoint: starts proxy + reporter
    proxy.py             # TLS termination, forwarding, block mode
    sampler.py           # sampling decision (monitor mode)
    classifier.py        # Classifier interface + implementations
    reporter.py          # aggregation + signing + report endpoints
    keys.py              # key generation (report signing key)
    logging_guard.py     # forbids logging of plaintext bodies
    config.py            # env var parsing
  classifiers/           # pluggable classifier definitions (plain text)
    ai_capabilities.md   # base implementation
    README.md            # how to write a new definition
  auditor/
    __init__.py
    cli.py               # `auditor verify`, `auditor report`, `auditor rollup`
    attestation.py       # wraps Tinfoil CLI/SDK verification
    verify.py            # signature checks
  traffic/
    generate.py          # sends labeled mix of prompts through the AIAB
    prompts_benign.jsonl
    prompts_capabilities.jsonl
  webchat/
    index.html           # the demo chat UI (single file)
    demo_prompts.json    # the two preset prompts
    entrypoint.sh        # writes config.js from env at container start
    Dockerfile           # nginx:alpine, pinned digest
  inference/
    docker-compose.yml   # llama.cpp server, local only
    README.md
  deploy/
    Dockerfile           # AIAB image, reproducible
    docker-compose.local.yml   # inference + aiab (monitor) + traffic, laptop mode
    docker-compose.demo.yml    # inference + aiab (block) + webchat, laptop mode
    tinfoil/             # Tinfoil Container configuration (follow their docs)
  demo/
    adversarial.sh       # Milestone 4 script
    README.md            # how to run the web chat demo, with the two prompts
  docs/
    THREAT_MODEL.md
    DEFERRED.md          # copy of section 10
    OPEN_QUESTIONS.md
  tests/
```

## 5. Component specifications

### 5.1 Proxy (`aiab/proxy.py`)

- Accepts `POST /v1/chat/completions`. Forwards to `INFERENCE_URL`. Returns the response unchanged to the client.
- Version 1 handles non-streaming requests only. If the request body has `"stream": true`, return HTTP 400 with a clear error. Log the count of rejected streaming requests (count only, never the body).
- Also accept `GET /v1/models` and forward it.
- Apart from the `/aiab/*` endpoints below and configured CORS preflights, every other path returns 404.
- **Local mode** (`AIAB_MODE=local`): the proxy terminates TLS itself. Generate a private key and self-signed certificate in memory at startup. Never write the private key to disk.
- **Tinfoil mode** (`AIAB_MODE=tinfoil`): follow the Tinfoil Containers docs for how TLS reaches the app. If the enclave terminates TLS and hands the app plain HTTP on an internal port, listen on that port. Record which model applies in `docs/OPEN_QUESTIONS.md` after reading the docs.
- **CORS:** when `CORS_ALLOW_ORIGIN` is set, add CORS headers to `/v1/*` and `/aiab/health` responses for that one origin and answer `OPTIONS` preflight requests. When unset, send no CORS headers. The demo compose file sets it to the webchat origin.
- `requests_total` counts validated, supported chat requests admitted to processing, including ones that later fail. Exclude model-list, health, audit, preflight, malformed, and unsupported requests. `requests_forwarded` counts requests dispatched upstream even if the upstream subsequently fails.
- Support text-only messages for version 1. Serialize the complete submitted message list with role labels as classifier input, including system/developer/assistant messages rather than only user messages. Reject unsupported tools, attachments, or non-text content with HTTP 400; do not silently omit content that the inference server would see. Use this serialization in both modes and evaluation. No cross-request session tracking is required.

**Enforcement modes** (`ENFORCEMENT_MODE`):

- `monitor` (default). Decide sampling at admission, independently of upstream success, and increment `requests_selected` if selected. Forward the request, then classify selected request/response pairs asynchronously without delaying the client response for classification. Selected requests that cannot be classified (including upstream failure or queue exhaustion) count as `classifier_errors`; never silently discard them. Bound the queue and processing time.
- `block`. Before forwarding, increment `requests_selected` and call `classifier.classify(prompt=serialized_messages, response=None)`. Every admitted request is selected; the sampler is not used. Then:
  - If `flagged` and `confidence >= BLOCK_THRESHOLD` (default `0.8`): do not forward. Return HTTP 403 with an OpenAI-style error body:
    ```json
    {"error": {"type": "policy_violation", "code": "aiab_blocked",
               "message": "Request blocked by AIAB policy: classified as AI capabilities research (confidence 0.93).",
               "classifier_id": "ai_capabilities/v1", "confidence": 0.93}}
    ```
    Increment `requests_blocked`.
  - Otherwise forward as normal.
  - If the classifier returns an error after one retry: return HTTP 503 and increment `classifier_errors`. Do not forward. Block mode fails closed.
- Block mode adds one classifier round trip of latency per request. Acceptable for the demo. Say so in `THREAT_MODEL.md`.

### 5.2 Sampler (`aiab/sampler.py`)

- Interface: `should_sample(request_id: str) -> bool`.
- Version 1: Bernoulli sampling with probability `SAMPLE_RATE` (default `0.05`). Use `secrets.SystemRandom()`.
- Used in `monitor` mode only.
- Keep the interface stable. A later version will replace this with verifiable sampling (see Deferred).

### 5.3 Classifier (`aiab/classifier.py`)

Define an interface:

```python
class Classifier(Protocol):
    id: str            # stable identifier, e.g. "ai_capabilities/v1"
    def classify(self, prompt: str, response: str | None) -> ClassifyResult: ...

@dataclass
class ClassifyResult:
    flagged: bool
    confidence: float  # 0.0–1.0
```

`response` is `None` in block mode (the response does not exist yet). The system prompt must handle both cases.

Implementations:

1. **`TinfoilLLMClassifier`** (primary). Uses the Tinfoil Python SDK to call the Tinfoil inference API. The SDK must be the only path to the API; do not call the REST endpoint directly, because the SDK performs attestation verification. Configuration:
   - `TINFOIL_API_KEY`
   - `CLASSIFIER_MODEL` (a model served by Tinfoil that supports JSON/structured output)
   - `CLASSIFIER_DEFINITION` (path to a file in `classifiers/`, default `classifiers/ai_capabilities.md`)
   - Build the request as: a fixed system prompt that contains the definition text and the output schema; a user message that contains the sampled prompt (and response, if present) inside clear delimiters, marked as data to classify and not as instructions.
   - Require the model to answer with JSON only: `{"flagged": true|false, "confidence": <0-1>}`. Parse strictly, including types and finite numeric range. Parsing failure is an error, never a successful unflagged result.
   - Timeouts and retries: one retry, then raise a classification error. Count one final error per selected request, not per attempt. Monitor mode records the failure; block mode returns 503 without forwarding. Attestation failure follows the stricter rule in section 7.
   - Confidence is an uncalibrated model score, not a probability of a policy violation. Delimiters and structured output are not a security boundary against prompt injection.
2. **`MockClassifier`** (local development only). Keyword-based. Flags text containing terms such as `pretrain`, `pretraining`, `fine-tune`, `fine-tuning`, `learning rate`, `RLHF`, `reward model`, `ablation`, `scaling law`, `MoE`, `loss per FLOP`, `training run`. Confidence `0.95` when two or more terms match, `0.6` when one matches, `0.0` otherwise. Used when `TINFOIL_API_KEY` is not set and `AIAB_MODE=local`. Never allow it in `tinfoil` mode; fail at startup if selected there.

Write `classifiers/ai_capabilities.md` as the base definition. It must describe, in plain language, what counts as "AI research or capabilities-increasing activity" (for example: designing training runs, ablation studies, architecture changes, RL or fine-tuning of frontier models, scaling experiments, eval design for frontier capabilities, agent scaffolding aimed at improving model capability) and what does not (general ML coursework, using a model for ordinary coding, discussion of AI policy). Instruct the classifier to give confidence above `0.8` only when the request is clearly about training, fine-tuning, or improving a model. Keep it to one page. Write `classifiers/README.md` explaining how to add another definition file.

### 5.4 Reporter (`aiab/reporter.py`)

- At startup, construct a non-secret policy manifest from effective values: manifest schema, classifier id and implementation (mock or Tinfoil), SHA-256 of the loaded definition and full classifier system prompt, requested model identifier, classifier endpoint and verification policy, generation parameters, input serialization version, enforcement mode, block threshold, sample rate/algorithm, report period/history, and processing timeout/queue limits. Include the governed inference origin without credentials, query strings, or secrets. Never copy arbitrary environment variables. Compute `policy_digest = SHA-256(canonical_manifest)`.
- `GET /aiab/manifest` returns that manifest. The attested application derives it from the exact frozen settings used at runtime. Fetching it over the attested channel binds these settings to the verified application; an image digest alone does not establish environment variable values. Compare against an independently approved digest. A requested model name does not prove immutable weights; document what classifier identity the provider actually attests.
- Aggregate into non-overlapping periods of `REPORT_PERIOD_SECONDS` (default `600`), assigned by request admission time; emit empty periods too. Finalize after a bounded processing deadline. Unfinished selected work becomes a classification error; late results must not revise signed reports or cause a timed-out block-mode request to be forwarded. Timestamps rely on the available clock, not a claim of trusted wall-clock time.
- Per period, produces one report with exactly these fields and nothing else:

```json
{
  "schema": "aiab-report/v1",
  "aiab_id": "<stable id from env>",
  "boot_id": "<random identifier generated at startup>",
  "sequence": 1,
  "policy_digest": "<SHA-256 of canonical policy manifest>",
  "classifier_id": "ai_capabilities/v1",
  "enforcement_mode": "monitor",
  "period_start": "<RFC3339>",
  "period_end": "<RFC3339>",
  "requests_total": 0,
  "requests_forwarded": 0,
  "requests_selected": 0,
  "requests_classified": 0,
  "requests_flagged": 0,
  "requests_blocked": 0,
  "classifier_errors": 0
}
```

- `enforcement_mode` is either `monitor` or `block`. All counters are non-negative integers. `requests_classified` counts successful classifications only; `requests_selected = requests_classified + classifier_errors` in finalized reports. In monitor mode, `requests_selected <= requests_total` and `requests_blocked = 0`. In block mode, `requests_selected = requests_total`. Always enforce `requests_blocked <= requests_flagged <= requests_classified` and `requests_forwarded + requests_blocked <= requests_total`.
- `sequence` starts at 1 and increases once per period within a boot. A new boot id and signing key explicitly start a new history; neither proves completeness of unseen history.
- Serialize with canonical JSON (sorted keys, no whitespace). Sign with Ed25519. Emit `{"report": <json string>, "signature": "<base64>"}`.
- The schema is fixed, not the byte length. Fixed-length padding and stronger output privacy remain deferred. Do not describe this as a proven bandwidth-constrained or leakage-free channel.
- Keep the last `REPORT_HISTORY` reports (default `100`) in memory. Nothing is written to disk.
- Endpoints (served by the same HTTP server as the proxy, on distinct paths):
  - `GET /aiab/pubkey` → the Ed25519 public key (base64), AIAB id, boot id, and policy digest.
  - `GET /aiab/manifest` → the non-secret effective policy manifest above.
  - `GET /aiab/reports/latest`
  - `GET /aiab/reports` → list of signed reports in memory.
  - `GET /aiab/health`
- The report must never contain request or response text, request IDs, user identifiers, or API keys. Add a test that asserts the report schema has exactly the fields above.

### 5.5 Keys (`aiab/keys.py`)

- Generate the Ed25519 report signing key at startup, in memory.
- Version 1 accepts that the key changes on every restart. The auditor links the key to the enclave by fetching `/aiab/pubkey` over a connection that the Tinfoil SDK/CLI has verified. Document this linkage in `THREAT_MODEL.md`.
- Reject inconsistent boot ids, policy digests, or signatures across fetched artifacts. A restart during fetching requires a fresh fetch, never mixing histories.
- Record in `docs/OPEN_QUESTIONS.md`: whether a Tinfoil Container can hold a persistent key across restarts, and how.

### 5.6 Logging guard (`aiab/logging_guard.py`)

- Never pass bodies or raw SDK/upstream exception text to the logger. Disable HTTP/SDK body debugging and log allowlisted error categories. A filter for body markers or long messages is only defense in depth; it cannot recognize arbitrary plaintext reliably.
- Set default log level to INFO. Log counts, timings, and errors only.
- Add a test that runs a request through the proxy with a unique sentinel string in the prompt and asserts the sentinel never appears in captured logs. Run this test in both enforcement modes.

### 5.7 Auditor (`auditor/cli.py`)

Commands:

- `auditor verify --host <aiab-host> --repo <owner/repo>`
  Runs Tinfoil attestation verification for the AIAB enclave (through the Tinfoil CLI or SDK — check the docs). Compares the measured digest to `EXPECTED_AIAB_DIGEST` or `--expected-digest`. Fetches the manifest over the attested channel and compares its digest to `EXPECTED_POLICY_DIGEST` or `--expected-policy-digest`. Require both expected values outside insecure local mode; never approve values merely because the server returned them. Print both digests; exit non-zero on mismatch. Reject debug-mode enclaves.
- `auditor report --host <aiab-host> --repo <owner/repo>`
  Applies the same image/policy checks as `verify`, fetches `/aiab/pubkey` and `/aiab/reports` over the attested channel, verifies signatures, boot/policy linkage, counter invariants, and sequence/time ordering, and prints reports. Never double-count duplicate reports.
- `auditor rollup --host ... --repo ...`
  Same as `report`, then prints one line per period:
  `"<period_start> – <period_end> [<mode>]: <S> selected of <T> admitted at this proxy; <N> successfully classified, <E> classification failures, <M> flagged (<P>% of successful classifications), <B> blocked; policy <digest>"`
  and totals for unique fetched periods within the same boot and policy. Use `N/A` when no classifications succeeded. Always print absolute counts and note that request share is not token/compute share or verified research prevalence. Small samples and classification failures limit interpretation; these are descriptive sample results, not population estimates.
- `report` and `rollup` accept optional `--state-file <path>`: one auditor-local JSON checkpoint with last verified AIAB/boot identity, key, policy digest, sequence, and period end. Compare fetched reports to the checkpoint; warn prominently about changed boot/key/policy, missing sequences, time gaps, or a latest report that has receded or is overdue. Save only after successful validation, without moving a same-boot checkpoint backward. On first use, say earlier continuity is unknown. Warnings mean incomplete evidence, not proof of malicious behavior. This cannot detect forks/restarts never observed by this auditor. No background collector or transparency service in version 1.
- Local mode: `--insecure-local` flag skips attestation and trusts the self-signed cert. Print a loud warning. Refuse this flag if the host is not `localhost` or `127.0.0.1`.

### 5.8 Traffic generator (`traffic/generate.py`)

- Reads the two JSONL prompt files. Each line: `{"prompt": "...", "label": "benign" | "capabilities"}`.
- Write about 60 prompts of each class. Capabilities prompts should look like real AI research work (training-run design, ablations, eval design, RL setups, scaling questions). Benign prompts should span coding help, writing, cooking, travel, general science. None of the prompts may contain actually hazardous content.
- Within those existing 120 examples, reserve at least 12 held-out challenge cases: benign ML coursework/policy discussions, indirect capabilities requests, and attempts to instruct the classifier to ignore its policy. Optional `category` and `split` fields are evaluation-only and never sent to the AIAB. Do not tune on held-out cases. Include paired examples where removing workflow context makes purpose ambiguous; report that limitation rather than inventing certainty.
- Sends them through the AIAB in random order at a configurable rate. Records each request's label locally in the generator only (the AIAB never sees the label).
- Flags: `--url`, `--count`, `--rate`, `--insecure` (for the self-signed cert in local mode).
- Prints a summary at the end. In monitor mode, `capabilities_fraction × sample_rate × count` estimates sampled capabilities requests, not classifier flags unless classification is perfect. In block mode, count policy 403s and classification 503s separately and compare outcomes to labeled requests.

### 5.9 Inference server (`inference/`)

- `docker-compose.yml` that runs `llama.cpp` server with a small instruct model on an OpenAI-compatible port. Document how to fetch the GGUF. Do not commit model weights.
- This server is not part of the trusted system. Say so in its README.

### 5.10 Web chat (`webchat/`)

The demo front end. Not part of the trusted system; it represents the operator's product.

- `index.html`: one file, inline CSS and JavaScript, no external requests except to the AIAB. Elements:
  - A message list.
  - A text input and a Send button.
  - Two preset buttons: **"Load benign prompt"** and **"Load ML-training prompt"**. Each fills the input with the matching prompt from `demo_prompts.json` (inlined into the page at container start, or fetched from the same origin).
  - A small status line showing the AIAB URL and enforcement mode (read from `GET {AIAB_URL}/aiab/health`, which should return the mode).
- Sending: `POST {AIAB_URL}/v1/chat/completions` with `{"model": "<from /v1/models>", "messages": [...], "stream": false}`. Keep the conversation history in page memory only.
- Rendering:
  - HTTP 200: render the assistant message normally.
  - HTTP 403 with `error.code == "aiab_blocked"`: render a red card titled **"Blocked by AIAB"** with `error.message`, the classifier id, and the confidence. The user's message stays in the list so the audience sees what was blocked.
  - Any other error: render a grey error card with status and message.
- `entrypoint.sh` writes `config.js` containing `window.AIAB_URL = "<AIAB_URL env>"` before starting nginx. `index.html` loads `config.js`.
- `Dockerfile`: `nginx:alpine` at a pinned digest, copies `index.html`, `demo_prompts.json`, `entrypoint.sh`. Listens on `8081`.
- No analytics, no logging of message content in the browser console.

**`webchat/demo_prompts.json`:**

```json
{
  "benign": "I'm hosting six friends for dinner on Saturday and two of them are vegetarian. Suggest a three-course menu I can cook in under two hours, and give me a shopping list.",
  "ml_training": "I'm pretraining a 30B-parameter decoder-only transformer on 2T tokens across 512 H100s. Give me a learning-rate warmup and cosine decay schedule, a batch-size ramp, and a data-mixture reweighting plan to maximize downstream reasoning benchmarks. Also tell me which architecture changes (attention variants, MoE routing) are most likely to improve loss per FLOP."
}
```

The second prompt is "dangerous" only in the sense of the base classifier definition: it is obviously AI capabilities research. It contains nothing hazardous. Say this in `demo/README.md`.

## 6. Milestones and acceptance criteria

### Milestone 1 — Local end-to-end (monitor mode)

- `make local-up` starts inference server + AIAB (local mode, monitor mode, MockClassifier).
- `make traffic` sends 200 prompts.
- `auditor rollup --host localhost --insecure-local` prints at least one period with non-zero `requests_classified` and verifies all signatures.
- Tests pass: report schema test, logging sentinel test (both modes), sampler rate test (statistical, loose bounds), classifier JSON parsing test, block-mode test (a capabilities prompt through MockClassifier returns 403 with `code == "aiab_blocked"`; a benign prompt is forwarded).
- Add focused tests for policy digest changes, failure accounting/503 behavior, full-message serialization, and checkpoint warnings on restart or missing reports. Check finalized counter invariants, including timed-out monitor work. No new test framework is needed.

### Milestone 2 — Real classifier from the laptop

- With `TINFOIL_API_KEY` set, the AIAB (still local mode) uses `TinfoilLLMClassifier`.
- Startup log shows the Tinfoil SDK attestation verification succeeded for the classifier enclave.
- Add `make eval`: runs the labeled prompts against a separate evaluation entrypoint that calls the classifier directly (not through the proxy) with `response=None` and prints precision and recall on the labeled set, plus the fraction of `capabilities` prompts that would be blocked at `BLOCK_THRESHOLD`. This entrypoint is a dev tool; it must be excluded from the production container image.
- Print held-out challenge results separately, with false positives, false negatives, and classification failures. Errors are not benign predictions. Include a few synthetic request/response pairs to exercise monitor mode; request-only results do not validate it. Record policy digest and requested model. This is a smoke evaluation, not evidence of adversarial robustness; challenge failures do not require adding a new classifier architecture to this demo.
- Both demo prompts, run through `make eval`, produce the expected result: benign not flagged; ML-training flagged with confidence ≥ 0.8.

### Milestone 3 — AIAB in a Tinfoil Container

- Reproducible Dockerfile. `make image` builds it; building twice yields the same digest. Document any step that breaks reproducibility.
- Deploy per `deploy/tinfoil/` and the Tinfoil docs. The AIAB has a public hostname.
- Publish the image digest, reviewed non-secret policy manifest, and its digest in `README.md`. Derive the expected manifest from approved settings independently of the live endpoint.
- `auditor verify --host <hostname> --repo <owner/repo> --expected-digest <digest> --expected-policy-digest <policy-digest>` succeeds.
- `auditor rollup` fetches reports over the attested channel and verifies signatures.
- Traffic generator runs from the laptop against the public hostname.

### Milestone 4 — Adversarial demo (`demo/adversarial.sh`)

Each step must visibly show either detection or a documented verification limit. Script them, with pauses and printed explanations; restore approved settings/deployment between steps:

1. **Tamper:** change one byte in `classifiers/ai_capabilities.md`, rebuild, redeploy. `auditor verify` fails on digest mismatch.
2. **Debug enclave:** redeploy as a debug enclave. `auditor verify` rejects it.
3. **Unattested classifier:** set `CLASSIFIER_ENDPOINT_OVERRIDE` to a plain HTTP endpoint. The AIAB must refuse at startup because only the SDK path is allowed. Show the error.
4. **Bypass:** send traffic directly to the inference server. Compare the generator's count to `requests_total`. Explicitly show that the auditor alone cannot detect these missing requests; the generator supplies independent ground truth only for the demo. Trusted coverage measurement is deferred.
5. **Configuration change:** change `BLOCK_THRESHOLD` without rebuilding the image, restart, and show that verification against the previous policy digest fails even though the image matches.
6. **Restart:** save an auditor checkpoint, restart with the same approved settings, wait for a finalized report, and show the new-boot/continuity warning. The checkpoint does not recover lost reports.

### Milestone 5 — Web chat demo (block mode)

- `make demo-up` starts inference + AIAB (`ENFORCEMENT_MODE=block`, `CORS_ALLOW_ORIGIN=http://localhost:8081`) + webchat, all local. Uses `TinfoilLLMClassifier` if `TINFOIL_API_KEY` is set, otherwise `MockClassifier`.
- Open `http://localhost:8081`. The status line shows mode `block`.
- Click **Load benign prompt**, send. A normal answer appears.
- Click **Load ML-training prompt**, send. The red **Blocked by AIAB** card appears with confidence ≥ 0.8. No response text from the inference server appears.
- Use a short reporting period in demo compose, wait for finalization, then `auditor rollup --host localhost --insecure-local` shows `requests_blocked >= 1`. Do not treat a live partial period as a finalized signed report.
- The same page works against the Tinfoil-deployed AIAB by setting `AIAB_URL` to the public hostname and `CORS_ALLOW_ORIGIN` on that deployment to the webchat origin. Document this in `demo/README.md` as the full demo sequence: verify → benign → blocked → rollup.

## 7. Engineering rules

- Type hints everywhere. `ruff` for lint and format. `pytest` for tests. `make check` runs all three.
- Small dependency set. Prefer the standard library. Justify each third-party package in `pyproject.toml` comments.
- No plaintext request or response content may ever be written to disk, logs, metrics, or the report. This is the core property. Every PR-sized chunk of work must keep the sentinel test passing.
- No secrets in the repo. `.env.example` lists every variable with a placeholder.
- Fail closed on classifier attestation failure: verify the classifier connection before accepting chat traffic at startup, and on a later failure stop admitting new requests with HTTP 503 until verification recovers. Never fall back to the mock classifier. In block mode, any classification error prevents forwarding (503). Monitor mode deliberately forwards before asynchronous classification; a later failure cannot undo requests already forwarded. State this limit explicitly.
- Write `docs/THREAT_MODEL.md` stating exactly what version 1 protects against and what it does not. Be honest. Include: Tinfoil is a party that hosts the hardware; the report key rotates per boot; sampling is not yet verifiable; the classifier is a general chat model, so prompt injection in sampled traffic is a known weakness; the bypass problem is not solved; block mode classifies every request, so the classifier sees 100% of prompts rather than a sample; block mode leaks the per-request decision to the requester (by design) and, through response size and timing, possibly to a network observer.
- Include section 1's claim boundaries, exact-count leakage, checkpoint limitations, and requested versus attested classifier identity. Inventory outbound destinations and observable channels (reports, HTTP responses, timing, logs); no analytics or arbitrary request-selected destinations. A sentinel test or attestation does not prove absence of all information leakage. State hardware/firmware and provider attestation assumptions explicitly.

## 8. Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `AIAB_MODE` | `local` or `tinfoil` | `local` |
| `AIAB_ID` | stable identifier in reports | `aiab-dev` |
| `ENFORCEMENT_MODE` | `monitor` or `block` | `monitor` |
| `BLOCK_THRESHOLD` | min confidence to block, block mode only | `0.8` |
| `INFERENCE_URL` | governed inference server | `http://inference:8080` |
| `LISTEN_PORT` | port the AIAB listens on | `8443` local / per Tinfoil docs |
| `CORS_ALLOW_ORIGIN` | single allowed origin for `/v1/*`; unset = no CORS | unset |
| `SAMPLE_RATE` | sampling probability, monitor mode only | `0.05` |
| `REPORT_PERIOD_SECONDS` | aggregation window | `600` |
| `REPORT_HISTORY` | reports kept in memory | `100` |
| `TINFOIL_API_KEY` | classifier API key | unset |
| `CLASSIFIER_MODEL` | Tinfoil model id | pick after reading docs |
| `CLASSIFIER_DEFINITION` | path to definition file | `classifiers/ai_capabilities.md` |
| `LOG_LEVEL` | logging level | `INFO` |
| `AIAB_URL` (webchat only) | AIAB base URL the page talks to | `https://localhost:8443` |
| `EXPECTED_AIAB_DIGEST` (auditor only) | approved image measurement; alternatively CLI flag | required outside insecure local mode |
| `EXPECTED_POLICY_DIGEST` (auditor only) | approved manifest digest; alternatively CLI flag | required outside insecure local mode |

`CLASSIFIER_ENDPOINT_OVERRIDE` is deliberately unsupported: fail startup if set. Choose the endpoint through the reviewed SDK integration. Document timeout, retry, queue, and generation constants and include their effective values in the manifest rather than adding tuning knobs for the demo.

## 9. Order of work

1. Repo skeleton, `pyproject.toml`, `Makefile`, `.env.example`, empty modules with docstrings; draft `THREAT_MODEL.md` from the claim boundaries before implementation.
2. `inference/docker-compose.yml` and confirm a local completion works.
3. Proxy (local TLS, both enforcement modes, CORS) + sampler + MockClassifier + reporter + keys + logging guard. Tests.
4. Auditor CLI with `--insecure-local`. Milestone 1 done.
5. Read Tinfoil SDK docs. Implement `TinfoilLLMClassifier`. `make eval`. Milestone 2 done.
6. Read Tinfoil Containers docs. Dockerfile, reproducibility check, deploy config, auditor attestation path. Milestone 3 done.
7. `demo/adversarial.sh`. Milestone 4 done.
8. `webchat/` + `docker-compose.demo.yml` + `demo/README.md`. Milestone 5 done.
9. `THREAT_MODEL.md`, `DEFERRED.md`, README polish.

Commit after each numbered step with a clear message.

## 10. Deferred (do not build now — keep interfaces open)

These came from a red-team review of the design. Record them in `docs/DEFERRED.md`. Where an interface is mentioned, keep it stable so the item can be added later.

**Coverage**
- Seal governed model weights to a measurement that includes the classifier, so use of those protected weights passes the classifier. Colocate classifier inside the inference TEE. This does not cover other models, copies, or hardware; datacenter-wide claims require separate inventory and trusted coverage controls.

**Sample frame**
- Two independent token counters (inference runtime + AIAB), both signed; mismatch is a finding within their established coverage. Explain why the runtime counter cannot itself be bypassed. *(Interface: reporter fields.)*
- Verifiable random sampling from an auditor-committed seed (HMAC or VRF). Sample by token, not request. *(Interface: `sampler.should_sample`.)*
- Auditor canary requests at a known rate; report canary count.
- Report absolute flagged token counts, not only ratios.

**Keys**
- Persistent report signing key bound into attestation user-data.
- Option: run the classifier in a GPU Tinfoil Container with a fine-tuned label-head model instead of a chat model.

**TEE hardening**
- Auditor-owned hardware in the operator's rack.
- Two TEE vendors, compare reports.
- k-of-n signing for code updates.
- Public transparency log (Sigstore Rekor) for measurements and reports.
- Short-lived session keys only.

**Classifier**
- Label-head classifier to reduce instruction-following attacks; evaluate adversarial evasion rather than assuming the architecture eliminates it.
- Ensemble of two different classifier types; report disagreement rate.
- Session-level grouping with bounded in-TEE state and attested deletion.
- Concentration statistic (how many hashed identities produce 90% of flags).

**Reporting**
- Differential privacy noise, minimum cell size, fixed byte-length reports and externally enforced release schedule. Version 1 has periodic reports but no constant-traffic or formal privacy guarantee.
- Durable auditor collection, transparency logs, and stronger rollback/fork protection beyond the optional last-seen checkpoint.
- Population estimates with sampling uncertainty and measured classifier error; token/compute weighting requires additional trusted measurements. Benign flooding can dilute request percentages even though it does not change selection probability at a fixed sample rate.
- Auditor pre-committed queries.
- Sealed evidence buffer with k-of-n threshold release when the flag rate crosses a threshold.

**Enforcement**
- Streaming support in block mode (classify the prompt before streaming starts).
- Block on response content as well as prompt (requires buffering the response).
- Per-tenant policies and thresholds.

**Governance**
- Mandatory open baseline classifier plus operator add-ons on separate report lines, each with a version hash.
- Legal: terms-of-service disclosure, data protection impact assessment per jurisdiction.

## 11. Open questions to resolve while building

Write answers into `docs/OPEN_QUESTIONS.md` as you find them.

1. In a Tinfoil Container, does the enclave terminate TLS, or does the app? What port does the app listen on?
2. Can a Tinfoil Container keep a signing key across restarts? If yes, how is it bound to attestation?
3. Are outbound connections from a Tinfoil Container to the Tinfoil inference API allowed by default?
4. Which Tinfoil-served models support strict JSON output, and what is the cheapest one that classifies well?
5. Does the Tinfoil Python SDK expose the verified measurement so the AIAB can log the classifier enclave digest at startup?
6. What is the classifier round-trip latency from a Tinfoil Container to the inference API? Block mode adds this to every request.
7. What does classifier attestation bind: serving code, routing configuration, model identifier, and/or immutable weight digest? Document the supported claim; do not equate a model alias with pinned weights.
