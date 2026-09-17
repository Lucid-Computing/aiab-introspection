# AIAB inference introspection demo

AIAB ("AI in a box") measures policy-relevant inference received by one proxy. The
planned appliance samples request/response pairs in monitor mode or classifies every
request before forwarding in block mode, then emits signed aggregate reports for an
auditor. The web demo illustrates endpoint enforcement with synthetic prompts.

This repository is under construction. The initial skeleton and claim boundaries
are in place; runtime commands below become available as the corresponding milestones
in [the implementation plan](aiab-plan.md) are completed.

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

Planned workflows: `make local-up`, `make traffic`, `make eval`, `make image`, and
`make demo-up`. Stop local containers with `make local-down` or `make demo-down`.
The auditor's `--insecure-local` mode is development-only, restricted to `localhost`
and `127.0.0.1`, and provides no enclave-attestation claim.

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
| `CLASSIFIER_MODEL` | Requested provider model identifier | Pending documentation and evaluation |
| `CLASSIFIER_DEFINITION` | Definition bundled into image | `classifiers/ai_capabilities.md` |
| `LOG_LEVEL` | Allowlisted operational log verbosity | `INFO` |
| `AIAB_URL` | Webchat's proxy base URL | `https://localhost:8443` |
| `EXPECTED_AIAB_DIGEST` | Auditor's independently approved image measurement | Required outside insecure local mode |
| `EXPECTED_POLICY_DIGEST` | Auditor's independently approved policy manifest digest | Required outside insecure local mode |

`CLASSIFIER_ENDPOINT_OVERRIDE` is deliberately unsupported and must fail startup if
set. The reviewed SDK integration chooses the classifier endpoint. Processing timeouts,
queue limits, retry policy, and generation settings will be documented constants in
the effective manifest, rather than additional tuning variables. One retry is allowed
for ordinary classification errors; attestation failure follows the stricter health
gate described in the threat model. The mock classifier is never permitted in a
Tinfoil deployment.

