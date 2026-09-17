# AIAB inference introspection demo

AIAB ("AI in a box") measures policy-relevant inference received by one proxy. The
appliance samples request/response pairs in monitor mode or classifies every
request before forwarding in block mode, then emits signed aggregate reports for an
auditor. The web demo illustrates endpoint enforcement with synthetic prompts.

The local runtime, Tinfoil SDK classifier,
synthetic traffic generator, direct classifier evaluation, and auditor with local
and attested transports are implemented. Tinfoil integration is covered by offline SDK tests;
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
Stop the local containers with `make local-down`. `make demo-up` starts the
implemented block-mode web chat stack; open `http://localhost:8081` and follow the
[demo sequence](demo/README.md) for certificate setup, benign and blocked prompts,
and a finalized rollup. Stop that stack with `make demo-down`.
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
Final exclusions keep application `__pycache__` directories and Python bytecode out
even after runtime source directories are re-included.

Pinned inputs alone do not guarantee byte-identical image digests from ordinary
`docker build`: platform selection, file and image timestamps, builder versions,
compression, and provenance attestations can change the output. With Docker,
Buildx, Bash and `jq` available, run the controlled comparison:

```sh
make image-reproducible
# Or use a checksum-verified Buildx executable kept outside your Docker setup:
AIAB_BUILDX_BIN=/absolute/path/to/docker-buildx make image-reproducible
```

The target requires Python 3 for inspecting OCI layers and uses a temporary isolated
Docker config and pinned BuildKit container,
performs two `--no-cache` builds for `linux/amd64` with timestamp rewriting and fixed
compression, and compares OCI manifest digests covering the config and layers.
It rejects Python bytecode and application cache directories in every exported
layer, retains evidence in a unique temporary directory, and removes its own builder.
On 2026-09-17 both clean builds matched at
`sha256:ca38ef042321b8507c790fc2d4002e594afa6fb4fd1e058dca4e7577985e1039`.
See the [method and validation record](docs/REPRODUCIBILITY.md) for tool checksums,
export settings and limits. This is local build evidence; no image has been published
and no Tinfoil deployment measurement is approved.

## Attested deployment and auditor (M3)

[deploy/tinfoil/](deploy/tinfoil/README.md) contains a template and complete release,
secret, network and verification instructions. Copy its template to root
`tinfoil-config.yml` only after filling the release-specific values. Tinfoil mode
serves plain HTTP on internal port 8080 behind the enclave shim and requires the
verified SDK classifier; the template uses non-root execution, a read-only root and
tmpfs caches. Its explicit open-public egress policy is a documented limitation
while the SDK's dynamic router/verification destination allowlist remains unresolved.

Outside `--insecure-local`, all three auditor commands require `--repo`, an
independently approved Tinfoil release digest and an independently approved policy
digest. The auditor invokes the current CLI and fetches identity, manifest and
reports using the SDK's verified TLS transport. `--expected-digest` means the
Tinfoil release's **`tinfoil.hash`/Sigstore subject digest**, not the Docker registry
image digest or a raw enclave register. The release config pins the application
image separately. See the deployment procedure for generating the manifest offline
with `python -m auditor.policy` and approving all three distinct values.

```sh
uv run --locked auditor verify --host HOSTNAME --repo OWNER/REPO \
  --expected-digest APPROVED_TINFOIL_RELEASE_DIGEST \
  --expected-policy-digest APPROVED_POLICY_DIGEST
```

There is no approved image/release/policy digest or public AIAB hostname to publish
yet. Live classification, deployment and attestation
acceptance remain outstanding.

## Adversarial demonstration (M4)

Run `bash demo/adversarial.sh` for a safe dry-run of all six scenarios: classifier
tampering, debug enclave rejection, unsupported classifier endpoint override,
direct inference bypass, changed threshold with the same image, and restart
continuity. It needs no deployment, credentials, or model. Run
`bash demo/adversarial.sh --execute --stage override` to exercise the actual local
startup configuration guard without contacting a classifier.

Other `--execute` stages require an already provisioned, independently approved
Tinfoil deployment, explicit public target/digest arguments, and interactive pauses
for manual changes and restoration. See [the driver guide](demo/ADVERSARIAL.md).
The driver runs real probes and checks expected outcomes; it does not build,
publish, or invent missing release inputs. Live adversarial deployment results
remain unperformed. Coverage gaps and lost history remain visible limitations,
and a generic verifier failure does not establish debug-policy causality.

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
| `LISTEN_PORT` | Listener port | `8443` local TLS; `8080` Tinfoil shim HTTP |
| `CORS_ALLOW_ORIGIN` | One allowed browser origin | Unset; no CORS |
| `SAMPLE_RATE` | Monitor sampling probability | `0.05` |
| `REPORT_PERIOD_SECONDS` | Non-overlapping report window | `600` |
| `REPORT_HISTORY` | Finalized reports held in memory | `100` |
| `TINFOIL_API_KEY` | Classifier API credential | Unset; mock allowed only locally |
| `CLASSIFIER_MODEL` | Requested provider model identifier | `gpt-oss-safeguard-120b` with a key; `mock-keywords/v1` without one |
| `CLASSIFIER_DEFINITION` | Definition bundled into image | `classifiers/ai_capabilities.md` |
| `LOG_LEVEL` | Allowlisted operational log verbosity | `INFO` |
| `AIAB_URL` | Webchat's proxy base URL | `https://localhost:8443` |
| `EXPECTED_AIAB_DIGEST` | Auditor's approved Tinfoil release digest (`tinfoil.hash`), distinct from OCI image digest | Required outside insecure local mode |
| `EXPECTED_POLICY_DIGEST` | Auditor's independently approved policy manifest digest | Required outside insecure local mode |

`CLASSIFIER_ENDPOINT_OVERRIDE` is deliberately unsupported and must fail startup if
set. The reviewed SDK integration chooses the classifier endpoint. Processing timeouts,
queue limits, retry policy, and generation settings are reviewed constants recorded
in the effective manifest. One retry is allowed
for ordinary classification errors; attestation failure follows the stricter health
gate described in the threat model. The mock classifier is never permitted in a
Tinfoil deployment.
