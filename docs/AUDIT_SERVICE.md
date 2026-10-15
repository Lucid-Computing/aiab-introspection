# Signed audit service

The service implements the October 14 meeting's minimal audit: both parties sign one plan, the box considers complete text spanning multiple prompts, and the plan program returns a bounded, schema-validated report. It does not measure general RSI capability. The original chat proxy remains a separate service.

## Live deployment and setup references

The audit API is deployed at **https://aiab-signed-audit.lucid-computing.containers.tinfoil.dev** (release `v0.1.3`). This is an API service, not a browser UI. Its public [health endpoint](https://aiab-signed-audit.lucid-computing.containers.tinfoil.dev/aiab/health) reports readiness; private operations require the verified client and a role credential.

- [Deployment setup](../static/deploy/tinfoil/audit/README.md): container ID/resources, credential location, release publication/update procedure and shutdown behavior.
- [Exact deployment pins](../static/deploy/tinfoil/audit/release.json): hostname, config repository, release digest and policy digest.
- [Repeatable live E2E command and results](../static/demo/audit/evidence/README.md).
- [Actual signed demo plan](../plan/demo-signed-plan.json).

For reusable evaluation, offline verification, result-capacity calculation and release automation, see [AUDIT_OPERATIONS.md](AUDIT_OPERATIONS.md). Explore a recorded run in the [interactive visualization](https://aiab-signed-audit-run.surge.sh).

## Architecture and trust

A CPU Tinfoil confidential VM runs the executor. It connects directly to a separately attested Tinfoil inference enclave using verified TLS. This architecture avoids provisioning a dedicated GPU. Both endpoints must be verified; ordinary HTTPS or a model name alone is insufficient. The executor checks the serving repository, exact release measurement, enclave host, verification steps and TLS key before transmitting text. Identity changes fail closed, with no retry or unverified fallback.

The reviewed safeguard serving release binds its modelwrap root to the mounted model weights. `static/aiab/audit/model.py` records this reviewed release-to-artifact relationship. A new serving release requires another review and an executor release. The signed plan also pins the inference host, release, artifact root, parameters and context budget. Attestation establishes execution identity, not classifier accuracy or absence of implementation vulnerabilities.

This is a single-agreement deployment with one independently configured lab key, auditor key and logger key. All accepted plans must name those keys. Demo keys exercise distinct signing roles but are locally provisioned test identities, not evidence that two independent organizations approved the policy. Production parties generate and retain their own private keys.

## Three interfaces

All private operations use the attestation-verifying `aiab-plan` client. It verifies an independently approved release and policy manifest before sending credentials or text. Public `/aiab/health`, `/aiab/manifest` and `/aiab/pubkey` support inspection. Fetching a manifest over ordinary HTTPS does not independently approve it.

| Operation | Body / response | Authorized role |
|---|---|---|
| `POST /aiab/plans` | `{plan, signatures: {lab, auditor}}`; returns plan digest, 201 new / 200 identical | Lab or auditor |
| `POST /aiab/runs` | `{submission_id, plan_digest, text, logger_evidence}`; returns run ID and boot ID, 202 | Lab or logger |
| `GET /aiab/runs/{run_id}/report` | 202 while running; 200 signed completed **or failed** envelope; 404 unknown | Lab or auditor |

Text is an opaque UTF-8 string: the executor does not impose a log-event format. An input submission is final immediately; there is no append/seal protocol. Retries with the same role, submission ID, plan and verified input return the same run; changed input returns 409. Report retrieval never reruns inference.

The Tinfoil shim must expose `/aiab/runs/*` as well as `/aiab/runs`. Role authentication is enforced inside the measured application. Requests are limited to 2 MiB, eight concurrent admission requests and a 15-second admission timeout. The service retains at most 16 plans and 128 runs, permits four active runs, and rejects capacity overflow. It keeps state in memory, without eviction. Restart changes the report key and boot ID, loses state, and requires deliberate registration/resubmission. There is no cross-restart one-execution guarantee.

## Minimal plan and configurable report

Machine-readable snapshots are in `schemas/`; the implementation constants in `static/aiab/audit/protocol.py` and `report.py` are authoritative. All objects reject unknown fields. JSON parsing rejects duplicate keys and non-finite numbers. Canonicalization is RFC 8785. Digests are `sha256:` plus SHA-256 of canonical JSON. Ed25519 keys/signatures use standard base64; signed bytes are `AIAB/<kind>/v1` followed by a NUL byte and canonical JSON, with kinds `plan`, `logger-export`, and `report`.

A plan contains the two public keys, runtime image digest, logger/time-window scope, pinned model, classification policy/examples/threshold/evaluation commitments, one program, an output schema and resource limits. Both signatures cover the entire plan. Updating any field requires both parties to sign again.

There is **one program**, not a separate report program:

```python
def run(text, model, policy):
    result = model.classify(text, policy)
    return {"flagged": result.score >= policy.threshold}
```

The runtime interprets a restricted Python AST; it never calls `exec`. Supported constructs are straight-line assignments, literals, fixed dictionaries, comparisons, conditional expressions and the single model capability. Imports, loops, reflection, filesystem/network access and other function calls are rejected. Exactly one classification call must execute. The implementation passes the signed policy and full text to the approved model and validates its numeric score. Prompt tokens are counted by the attested serving tokenizer before inference. Oversized input fails without truncation.

The plan's `output_schema` is JSON Schema draft 2020-12 restricted to closed objects, booleans, bounded integers and string enums. Arrays, free text, external references and other constructs are deliberately unsupported. For example:

```json
{"type":"object","properties":{"flagged":{"type":"boolean"}},"required":["flagged"],"additionalProperties":false}
```

The fixed report envelope has `schema: aiab-report/v2`, `run_id`, `boot_id`, `plan_digest`, `input_commitment`, `policy_digest`, and either `status: completed` with `result`, or `status: failed` with a fixed `error` code. The enclosing object has exactly `report` and `signature`. Invalid output becomes a signed `invalid_output` failure, never a clean result or raw program output. The client independently verifies signature, boot, plan, input, policy and output schema.

One boolean constrains the result to one bit; timing, metadata, errors and repeated submissions remain observable. This is not a total one-bit leakage guarantee or a proof against covert channels. Temperature zero does not guarantee deterministic inference. No appeals are implemented.

## Mock logger and eventual integration

The user explicitly authorized a mock logger because the real logger is not ready. `static/demo/audit/mock_logger.py` signs synthetic exports with real Ed25519 and exact-byte SHA-256 chaining. The deployed verifier has no unsigned-input bypass and no model stub.

`aiab-logger-export/v1` binds the exact concatenated text bytes, ordered chunk chain, sequence, previous head, count, time window and source key. The input commitment binds scope, text digest and signed statement digest. `complete: true` is a trusted exporter's signed assertion; it does not independently establish that every real event was captured or that a Rekor/WORM checkpoint was verified. A malicious authorized logger could omit events before signing.

When the real logger is ready, implement the existing `InputVerifier.verify(text, evidence, scope) -> commitment` interface against its native signatures/checkpoints and deterministic text transformation, or have a trusted exporter verify native evidence before signing this export. Keep the top-level text interface. Update the measured manifest/evidence version and review it with both parties. Native logger completeness remains untested until real fixtures exist.

## Run and verify

From `static/`:

```sh
uv sync --locked
uv run aiab-plan --help
uv run aiab-plan keygen --private lab.key --public lab.pub
uv run aiab-plan sign --plan plan.json --private lab.key --role lab --output lab.sig
# Auditor separately signs the identical plan with their own key.
uv run aiab-plan assemble --plan plan.json --lab-signature lab.sig --auditor-signature auditor.sig --output signed-plan.json
```

`register`, `submit`, and `report` each require `--host`, `--repo`, `--release-digest`, `--policy-digest`, `--token-file` and `--output`. Register/submit additionally take `--input`. Report takes `--run-id`, `--plan` and `--input-commitment`. Output files must not already exist. Keys and tokens must be protected private files; never put them in a plan or command argument.

`uv run python demo/audit/acceptance.py --help` describes the real Tinfoil acceptance driver. It exercises signed plan registration, tamper rejection, real confidential classification of three synthetic cases, idempotency and schema-invalid output rejection. Its small fixture set is a smoke test, not a robust RSI evaluation. A final plan must commit to the actual evaluation record before it is signed.

Deploy with `python -m aiab.audit.main`. Supply measured `AIAB_AUDIT_TRUST_JSON` (or `AIAB_AUDIT_TRUST_FILE`) plus separately provisioned `TINFOIL_API_KEY`, `AIAB_LAB_TOKEN`, `AIAB_AUDITOR_TOKEN`, `AIAB_LOGGER_TOKEN`. Trust pins the published runtime image digest; first build the generic executor, then generate trust/manifest and sign plans. Startup fails if attested inference cannot be verified. `/aiab/health` becomes unavailable after a model transport failure.

## Review limits

The distributed CPU/GPU design adds a network dependency and two enclave trust boundaries. It preserves the intended confidentiality boundary only while the pinned code, model-binding review, Tinfoil verification implementation, hardware trust roots and confidential serving environment hold. This implementation has adversarial tests, but has not had an independent security review. Raw text is not deliberately persisted; releasing Python references is not a cryptographic memory wipe. Private source/image access must be provided to both reviewing parties; public release metadata alone does not provide source access.
