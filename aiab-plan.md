# AIAB Demo — Implementation Plan

This file is the instruction set for setting up the repository. Read it fully before writing code.

## 1. Goal

Build a working demo of privacy-preserving inference introspection.

An appliance (the **AIAB**, "AI in a box") sits in front of an AI inference server. It runs inside a secure enclave (TEE). It:

1. Terminates TLS and forwards requests to the inference server.
2. Randomly samples a fraction of request/response pairs (monitor mode), or classifies every request before forwarding (block mode).
3. Classifies with an LLM that also runs inside an attested enclave.
4. Emits a small, fixed-size, signed report per period: how many requests were seen, how many were classified, how many were flagged, how many were blocked.

An **auditor** verifies that the AIAB runs the published code, fetches the reports over an attested channel, checks the signatures, and prints a rollup such as: "12% of sampled inference traffic in the last period was classified as AI capabilities research."

The final demo is a **web chat interface** that talks to the inference server through the AIAB in block mode. A benign prompt gets a normal answer. An obviously ML-training-related prompt is rejected with a visible "Blocked by AIAB" message. The auditor rollup then shows the block count.

The classifier definition is a pluggable text file. The base implementation targets "AI research / capabilities-increasing activity." Other definitions (for example, dangerous biology) plug in the same way.

## 2. Scope for this version

Build the base design only. Do **not** build the items in section 10 (Deferred). Leave clean interfaces so they can be added later.

In scope:

- Local end-to-end run on a laptop (Milestone 1).
- Real classifier via the Tinfoil inference API (Milestone 2).
- AIAB deployed as a Tinfoil Container with attestation verified by the auditor (Milestone 3).
- Adversarial demo script (Milestone 4).
- Web chat demo with block mode and two preset prompts (Milestone 5).

Out of scope: everything in section 10.

## 3. Platform decisions

- **TEE provider:** Tinfoil (https://tinfoil.sh). The AIAB runs as a Tinfoil Container. The classifier is called through the Tinfoil inference API using the Tinfoil SDK, which verifies the inference enclave's attestation on every connection.
- **Language:** Python 3.12. Use `uv` for dependency management with a committed lockfile.
- **Governed inference server:** any OpenAI-compatible server running a small open instruct model. Default: `llama.cpp` server (`llama-server`) with a 1B–3B GGUF model. Runs on a plain machine, not in an enclave. It plays the role of "the operator's model."
- **Web chat:** one static HTML file with inline CSS and JavaScript. No framework, no build step. Served by a pinned `nginx:alpine` container. It plays the role of "the operator's product."
- **Container:** one Dockerfile for the AIAB with a pinned base image digest and pinned dependencies. The build must be reproducible: same source → same image digest.
- **Config:** environment variables only. No config files with secrets. Document every variable in `README.md`.

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
- Every other path returns 404.
- **Local mode** (`AIAB_MODE=local`): the proxy terminates TLS itself. Generate a private key and self-signed certificate in memory at startup. Never write the private key to disk.
- **Tinfoil mode** (`AIAB_MODE=tinfoil`): follow the Tinfoil Containers docs for how TLS reaches the app. If the enclave terminates TLS and hands the app plain HTTP on an internal port, listen on that port. Record which model applies in `docs/OPEN_QUESTIONS.md` after reading the docs.
- **CORS:** when `CORS_ALLOW_ORIGIN` is set, add CORS headers to `/v1/*` responses for that one origin and answer `OPTIONS` preflight requests. When unset, send no CORS headers. The demo compose file sets it to the webchat origin.
- Count total requests and total forwarded requests. These counts go into the report.

**Enforcement modes** (`ENFORCEMENT_MODE`):

- `monitor` (default). Forward the request. After the response is sent to the client, hand the `(request_body, response_body)` pair to the sampler. Classify only sampled pairs, asynchronously, so classification adds no latency.
- `block`. Before forwarding, extract the text of all `user`-role messages from the request and call `classifier.classify(prompt=text, response=None)`. Every request is classified; the sampler is not used. Then:
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
   - Require the model to answer with JSON only: `{"flagged": true|false, "confidence": <0-1>}`. Parse strictly. If parsing fails, count it as `classifier_error` and do not flag.
   - Timeouts and retries: one retry, then count as `classifier_error`.
2. **`MockClassifier`** (local development only). Keyword-based. Flags text containing terms such as `pretrain`, `pretraining`, `fine-tune`, `fine-tuning`, `learning rate`, `RLHF`, `reward model`, `ablation`, `scaling law`, `MoE`, `loss per FLOP`, `training run`. Confidence `0.95` when two or more terms match, `0.6` when one matches, `0.0` otherwise. Used when `TINFOIL_API_KEY` is not set and `AIAB_MODE=local`. Never allow it in `tinfoil` mode; fail at startup if selected there.

Write `classifiers/ai_capabilities.md` as the base definition. It must describe, in plain language, what counts as "AI research or capabilities-increasing activity" (for example: designing training runs, ablation studies, architecture changes, RL or fine-tuning of frontier models, scaling experiments, eval design for frontier capabilities, agent scaffolding aimed at improving model capability) and what does not (general ML coursework, using a model for ordinary coding, discussion of AI policy). Instruct the classifier to give confidence above `0.8` only when the request is clearly about training, fine-tuning, or improving a model. Keep it to one page. Write `classifiers/README.md` explaining how to add another definition file.

### 5.4 Reporter (`aiab/reporter.py`)

- Aggregates over a fixed period `REPORT_PERIOD_SECONDS` (default `600`).
- Per period, produces one report with exactly these fields and nothing else:

```json
{
  "schema": "aiab-report/v1",
  "aiab_id": "<stable id from env>",
  "classifier_id": "ai_capabilities/v1",
  "enforcement_mode": "monitor" | "block",
  "period_start": "<RFC3339>",
  "period_end": "<RFC3339>",
  "requests_total": 0,
  "requests_classified": 0,
  "requests_flagged": 0,
  "requests_blocked": 0,
  "classifier_errors": 0
}
```

- In `monitor` mode, `requests_classified` is the number of sampled pairs and `requests_blocked` is always `0`. In `block` mode, `requests_classified` equals `requests_total`.
- Serialize with canonical JSON (sorted keys, no whitespace). Sign with Ed25519. Emit `{"report": <json string>, "signature": "<base64>"}`.
- Keep the last `REPORT_HISTORY` reports (default `100`) in memory. Nothing is written to disk.
- Endpoints (served by the same HTTP server as the proxy, on distinct paths):
  - `GET /aiab/pubkey` → the Ed25519 public key (base64) and the AIAB id.
  - `GET /aiab/reports/latest`
  - `GET /aiab/reports` → list of signed reports in memory.
  - `GET /aiab/health`
- The report must never contain request or response text, request IDs, user identifiers, or API keys. Add a test that asserts the report schema has exactly the fields above.

### 5.5 Keys (`aiab/keys.py`)

- Generate the Ed25519 report signing key at startup, in memory.
- Version 1 accepts that the key changes on every restart. The auditor links the key to the enclave by fetching `/aiab/pubkey` over a connection that the Tinfoil SDK/CLI has verified. Document this linkage in `THREAT_MODEL.md`.
- Record in `docs/OPEN_QUESTIONS.md`: whether a Tinfoil Container can hold a persistent key across restarts, and how.

### 5.6 Logging guard (`aiab/logging_guard.py`)

- Install a logging filter at startup that rejects any log record containing a request or response body. Practical approach: never pass bodies to the logger; add a filter that drops records longer than 1,000 characters and records containing the marker strings used for bodies.
- Set default log level to INFO. Log counts, timings, and errors only.
- Add a test that runs a request through the proxy with a unique sentinel string in the prompt and asserts the sentinel never appears in captured logs. Run this test in both enforcement modes.

### 5.7 Auditor (`auditor/cli.py`)

Commands:

- `auditor verify --host <aiab-host> --repo <owner/repo>`
  Runs Tinfoil attestation verification for the AIAB enclave (through the Tinfoil CLI or SDK — check the docs for which is better to call from Python). Prints the measured digest. Compares it to `EXPECTED_AIAB_DIGEST` from env or a `--expected-digest` flag. Exits non-zero on mismatch. Rejects debug-mode enclaves.
- `auditor report --host <aiab-host> --repo <owner/repo>`
  Establishes the attested connection, fetches `/aiab/pubkey` and `/aiab/reports`, verifies every signature against the fetched public key, and prints the reports.
- `auditor rollup --host ... --repo ...`
  Same as `report`, then prints one line per period:
  `"<period_start> – <period_end> [<mode>]: <N> classified of <T> total, <M> flagged (<P>%), <B> blocked, classifier <id>"`
  and a total line across all fetched periods.
- Local mode: `--insecure-local` flag skips attestation and trusts the self-signed cert. Print a loud warning. Refuse this flag if the host is not `localhost` or `127.0.0.1`.

### 5.8 Traffic generator (`traffic/generate.py`)

- Reads the two JSONL prompt files. Each line: `{"prompt": "...", "label": "benign" | "capabilities"}`.
- Write about 60 prompts of each class. Capabilities prompts should look like real AI research work (training-run design, ablations, eval design, RL setups, scaling questions). Benign prompts should span coding help, writing, cooking, travel, general science. None of the prompts may contain actually hazardous content.
- Sends them through the AIAB in random order at a configurable rate. Records each request's label locally in the generator only (the AIAB never sees the label).
- Flags: `--url`, `--count`, `--rate`, `--insecure` (for the self-signed cert in local mode).
- Prints a summary at the end. In monitor mode it estimates the expected flag count as `capabilities_fraction × sample_rate × count`. In block mode it counts HTTP 403 responses with `code == "aiab_blocked"` and compares them to the number of `capabilities` prompts sent.

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

### Milestone 2 — Real classifier from the laptop

- With `TINFOIL_API_KEY` set, the AIAB (still local mode) uses `TinfoilLLMClassifier`.
- Startup log shows the Tinfoil SDK attestation verification succeeded for the classifier enclave.
- Add `make eval`: runs the labeled prompts against a separate evaluation entrypoint that calls the classifier directly (not through the proxy) with `response=None` and prints precision and recall on the labeled set, plus the fraction of `capabilities` prompts that would be blocked at `BLOCK_THRESHOLD`. This entrypoint is a dev tool; it must be excluded from the production container image.
- Both demo prompts, run through `make eval`, produce the expected result: benign not flagged; ML-training flagged with confidence ≥ 0.8.

### Milestone 3 — AIAB in a Tinfoil Container

- Reproducible Dockerfile. `make image` builds it; building twice yields the same digest. Document any step that breaks reproducibility.
- Deploy per `deploy/tinfoil/` and the Tinfoil docs. The AIAB has a public hostname.
- Publish the image digest in `README.md`.
- `auditor verify --host <hostname> --repo <owner/repo> --expected-digest <digest>` succeeds.
- `auditor rollup` fetches reports over the attested channel and verifies signatures.
- Traffic generator runs from the laptop against the public hostname.

### Milestone 4 — Adversarial demo (`demo/adversarial.sh`)

Each step must produce a visible failure. Script them, with pauses and printed explanations:

1. **Tamper:** change one byte in `classifiers/ai_capabilities.md`, rebuild, redeploy. `auditor verify` fails on digest mismatch.
2. **Debug enclave:** redeploy as a debug enclave. `auditor verify` rejects it.
3. **Unattested classifier:** set `CLASSIFIER_ENDPOINT_OVERRIDE` to a plain HTTP endpoint. The AIAB must refuse at startup because only the SDK path is allowed. Show the error.
4. **Bypass:** send some traffic directly to the inference server, past the AIAB. Show that `requests_total` in the report is lower than the generator's count. (Explain that the fix — an independent counter — is in Deferred.)

### Milestone 5 — Web chat demo (block mode)

- `make demo-up` starts inference + AIAB (`ENFORCEMENT_MODE=block`, `CORS_ALLOW_ORIGIN=http://localhost:8081`) + webchat, all local. Uses `TinfoilLLMClassifier` if `TINFOIL_API_KEY` is set, otherwise `MockClassifier`.
- Open `http://localhost:8081`. The status line shows mode `block`.
- Click **Load benign prompt**, send. A normal answer appears.
- Click **Load ML-training prompt**, send. The red **Blocked by AIAB** card appears with confidence ≥ 0.8. No response text from the inference server appears.
- `auditor rollup --host localhost --insecure-local` shows `requests_blocked >= 1` for the current period.
- The same page works against the Tinfoil-deployed AIAB by setting `AIAB_URL` to the public hostname and `CORS_ALLOW_ORIGIN` on that deployment to the webchat origin. Document this in `demo/README.md` as the full demo sequence: verify → benign → blocked → rollup.

## 7. Engineering rules

- Type hints everywhere. `ruff` for lint and format. `pytest` for tests. `make check` runs all three.
- Small dependency set. Prefer the standard library. Justify each third-party package in `pyproject.toml` comments.
- No plaintext request or response content may ever be written to disk, logs, metrics, or the report. This is the core property. Every PR-sized chunk of work must keep the sentinel test passing.
- No secrets in the repo. `.env.example` lists every variable with a placeholder.
- Fail closed. If attestation verification of the classifier enclave fails, the AIAB stops accepting traffic and returns HTTP 503. It does not fall back to forwarding without classification and it does not fall back to the mock classifier. In block mode, a classifier error also fails closed (503).
- Write `docs/THREAT_MODEL.md` stating exactly what version 1 protects against and what it does not. Be honest. Include: Tinfoil is a party that hosts the hardware; the report key rotates per boot; sampling is not yet verifiable; the classifier is a general chat model, so prompt injection in sampled traffic is a known weakness; the bypass problem is not solved; block mode classifies every request, so the classifier sees 100% of prompts rather than a sample; block mode leaks the per-request decision to the requester (by design) and, through response size and timing, possibly to a network observer.

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

## 9. Order of work

1. Repo skeleton, `pyproject.toml`, `Makefile`, `.env.example`, empty modules with docstrings.
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
- Seal model weights to a measurement that includes the classifier, so all inference (internal and external) passes the classifier. Colocated classifier inside the inference TEE.

**Sample frame**
- Two independent token counters (inference runtime + AIAB), both signed; mismatch is a finding. *(Interface: reporter fields.)*
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
- Label-head classifier (no instruction channel) to resist prompt injection.
- Ensemble of two different classifier types; report disagreement rate.
- Session-level grouping with bounded in-TEE state and attested deletion.
- Concentration statistic (how many hashed identities produce 90% of flags).

**Reporting**
- Differential privacy noise, minimum cell size, fixed report size and schedule.
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
