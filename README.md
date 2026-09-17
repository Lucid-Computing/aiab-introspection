# AIAB inference introspection demo

AIAB ("AI in a box") measures policy-relevant inference received by one proxy. The
planned appliance samples request/response pairs in monitor mode or classifies every
request before forwarding in block mode, then emits signed aggregate reports for an
auditor. The web demo illustrates endpoint enforcement with synthetic prompts.

This repository is under construction. The local runtime, Tinfoil SDK classifier,
synthetic traffic generator, direct classifier evaluation, and signature-checking
local auditor are implemented. Tinfoil integration is covered by offline SDK tests;
live keyed classification and attested deployment have not been run. See
[the implementation plan](aiab-plan.md).

## What the demonstration establishes

With correctly verified deployment and under the TEE assumptions, evidence concerns
traffic admitted at this proxy and its effective policy. It does not establish
datacenter-wide coverage, absence of prohibited activity, or the fraction of compute
spent on AI research. Other endpoints and internal agent activity are outside the
observation boundary. A preset prompt being blocked does not demonstrate robust
detection of adversarial or distributed capabilities research.

Conversation contents should stay inside the appliance/classifier enclaves and the
governed inference server. That server still receives forwarded conversations.
Exact counts can disclose information in small periods; there is no differential
privacy or general covert-channel guarantee. Use synthetic traffic only. See the
[threat model](docs/THREAT_MODEL.md), [deferred work](docs/DEFERRED.md), and
[open questions](docs/OPEN_QUESTIONS.md).

## Development

Use Python 3.12 and `uv`. Dependency versions are resolved in `uv.lock`.
`make sync` installs the locked environment; `make check` runs Ruff lint, format
verification, and pytest. Package commands are `aiab` and `auditor`.

Local workflows: `make local-up`, `make traffic`, `make eval`, and `make image`.
Stop the local containers with `make local-down`. `make demo-up` remains planned.
`make eval` calls the classifier directly and reports development and held-out
metrics, classification errors, synthetic request/response pairs, the policy digest,
and requested model. With `TINFOIL_API_KEY` set, the classifier uses the Tinfoil SDK
and requests `gpt-oss-safeguard-120b` by default; without a key, local mode uses the
keyword mock. Live provider accuracy and latency remain unmeasured.
The auditor's `--insecure-local` mode is development-only, restricted to `localhost`
and `127.0.0.1`, and provides no enclave-attestation claim.

## Local monitor demo (M1)

Install Docker Engine, Docker Compose with `--wait` support, Python 3.12, and `uv`.
Obtain an instruct GGUF using the pinned download in
[inference/README.md](inference/README.md#obtain-a-model), or use an existing model.
The default mount is `models/qwen2.5-1.5b-instruct-q4_k_m.gguf`; to select another
file, export `INFERENCE_MODEL_PATH` with its absolute path. The stack never downloads
weights or creates a missing bind-mount path. Model weights stay outside the image.

Run these commands from the repository root after the model is present:

```sh
make sync
make local-up
curl --fail --silent --show-error --insecure https://127.0.0.1:8443/aiab/health
make traffic
# A period lasts 10 seconds, followed by a 30-second processing allowance.
# Wait after traffic ends so its final period has time to become signed.
sleep 45
uv run --locked auditor rollup --host localhost --insecure-local \
  --state-file /tmp/aiab-local-monitor-checkpoint.json
make local-down
```

`make traffic` sends 200 synthetic prompts, with each unused response capped at
32 generated tokens to bound CPU work. The local stack samples every admitted
request (`SAMPLE_RATE=1.0`) so a successful completion produces observable mock
classification counts. This setting is for deterministic demo coverage; the ordinary
runtime default remains 5%. The stack runs local monitor mode with an empty API key
and `MockClassifier`. Look for non-zero `requests_classified` in the verified rollup;
HTTP success alone does not establish that classification succeeded. Upstream
timeouts or slow CPU generation can produce errors: the inference timeout is 15
seconds per request, and retained history covers about 2 hours 46 minutes.

The HTTPS listener is published only on `127.0.0.1:8443`; inference is reachable
only inside this stack at `http://inference:8080`. Its separate Compose project name
does not reuse the standalone inference stack or host port 8080. `local-up` waits
for model loading and AIAB health. Both Make targets explicitly use
`--env-file /dev/null`, so the project `.env` is never loaded; a shell-exported model
path is still honored. `make local-down` preserves the downloaded model. For a
standalone Compose binary, use `make COMPOSE=/path/to/docker-compose local-up`
and the same override for `local-down`.

TLS certificates, signing keys, transcripts, and aggregate state remain in process
memory. AIAB runs as a non-root user with a read-only root filesystem and no data
volume. The auditor checkpoint is separate local verifier state. This sequence is
an acceptance procedure; configuration validation alone is not a completed live
model demonstration. See [local runtime](docs/LOCAL_RUNTIME.md) and
[local auditor](docs/LOCAL_AUDITOR.md) for the verification boundaries.

## Container build and reproducibility

`make image` builds the core appliance as `aiab:local`. The
[Dockerfile](deploy/Dockerfile) pins the official Python `3.12.13-slim-trixie` base
and uv `0.11.6` tool image by immutable multi-platform digest, verified against the
[Python tag API](https://hub.docker.com/v2/repositories/library/python/tags/3.12.13-slim-trixie)
and [uv tag API](https://hub.docker.com/v2/repositories/astral/uv/tags/0.11.6).
`uv sync --locked --no-dev --no-install-project --no-build` installs only locked
production wheels. A platform without compatible wheels fails instead of resolving
unlocked source-build tools. The image includes the core source and bundled
classifier definitions; build tools, tests, auditor, traffic tools, secrets, and
weights are excluded from the application payload. The build-context allowlist
also prevents local secret files and model weights from reaching the builder.

Pinned inputs alone do not guarantee byte-identical image digests from ordinary
`docker build`: platform selection, file and image timestamps, builder versions,
compression, and provenance attestations can change the output. For a reproducibility
check, use the same reviewed source, target platform, Buildx/BuildKit versions, and
export settings for two clean builds. Select a `docker-container` Buildx builder
(the default `docker` driver cannot export OCI archives) and a BuildKit version
supporting timestamp rewriting, then run:

```sh
docker buildx build --no-cache --platform linux/amd64 --provenance=false --sbom=false \
  --build-arg SOURCE_DATE_EPOCH=0 -f deploy/Dockerfile \
  --output type=oci,dest=/tmp/aiab-build-1.tar,rewrite-timestamp=true \
  --metadata-file /tmp/aiab-build-1.json .
docker buildx build --no-cache --platform linux/amd64 --provenance=false --sbom=false \
  --build-arg SOURCE_DATE_EPOCH=0 -f deploy/Dockerfile \
  --output type=oci,dest=/tmp/aiab-build-2.tar,rewrite-timestamp=true \
  --metadata-file /tmp/aiab-build-2.json .
jq -r '."containerimage.digest"' /tmp/aiab-build-1.json /tmp/aiab-build-2.json
```

Compare the exported image digests, not just a cache hit or archive file timestamps.
See Docker's [reproducible-build controls](https://docs.docker.com/build/ci/github-actions/reproducible-builds/)
and [OCI exporter timestamp option](https://docs.docker.com/build/exporters/oci-docker/).
This repository does not yet publish an independently verified reproducible or
attested deployment measurement.

## Configuration

Configuration comes from environment variables and is parsed once per boot. No
secret configuration is committed. `.env.example` lists the supported variables;
copying it does not automatically load it into a process. Never publish API keys.
No real deployment measurements are approved or published yet.

| Variable | Purpose | Default |
|---|---|---|
| `AIAB_MODE` | `local` or `tinfoil` | `local` |
| `AIAB_ID` | Stable report identity | `aiab-dev` |
| `ENFORCEMENT_MODE` | `monitor` or `block` | `monitor` |
| `BLOCK_THRESHOLD` | Minimum flagged confidence to block | `0.8` |
| `INFERENCE_URL` | Fixed governed inference server origin | `http://inference:8080` |
| `LISTEN_PORT` | Listener port | `8443` locally; deployment port pending documentation |
| `CORS_ALLOW_ORIGIN` | One allowed browser origin | Unset; no CORS |
| `SAMPLE_RATE` | Monitor sampling probability | `0.05` |
| `REPORT_PERIOD_SECONDS` | Non-overlapping report window | `600` |
| `REPORT_HISTORY` | Finalized reports held in memory | `100` |
| `TINFOIL_API_KEY` | Classifier API credential | Unset; mock allowed only locally |
| `CLASSIFIER_MODEL` | Requested provider model identifier | `gpt-oss-safeguard-120b` with a key; `mock-keywords/v1` without one |
| `CLASSIFIER_DEFINITION` | Definition bundled into image | `classifiers/ai_capabilities.md` |
| `LOG_LEVEL` | Allowlisted operational log verbosity | `INFO` |
| `AIAB_URL` | Webchat's proxy base URL | `https://localhost:8443` |
| `EXPECTED_AIAB_DIGEST` | Auditor's independently approved image measurement | Required outside insecure local mode |
| `EXPECTED_POLICY_DIGEST` | Auditor's independently approved policy manifest digest | Required outside insecure local mode |

`CLASSIFIER_ENDPOINT_OVERRIDE` is deliberately unsupported and must fail startup if
set. The reviewed SDK integration chooses the classifier endpoint. Processing timeouts,
queue limits, retry policy, and generation settings are reviewed constants recorded
in the effective manifest. One retry is allowed
for ordinary classification errors; attestation failure follows the stricter health
gate described in the threat model. The mock classifier is never permitted in a
Tinfoil deployment.
