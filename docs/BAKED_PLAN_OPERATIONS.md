# Folder-based audit operations

Current deployed release: **v0.2.2**. [Deployment pins](../static/deploy/tinfoil/audit/release.json) and [live acceptance evidence](../static/demo/audit/baked-evidence/README.md).

The v0.5 runtime runs one jointly signed `plan/` bundle per image. The six functions
in `plan/plan.py` now run through the static runtime, in order: filter, group into
queries, classify each query, aggregate scores, emit result. Appeals remain an
explicitly uncalled post-demo function. The UI and native logger are separate
workstreams. The broader mandatory requirements are tracked in
[REQUIREMENTS_COMPLIANCE.md](REQUIREMENTS_COMPLIANCE.md).

Machine-readable [plan](../schemas/baked-plan-v1.schema.json), [submission](../schemas/record-submission-v1.schema.json), [logger evidence](../schemas/record-export-evidence-v1.schema.json) and [report](../schemas/report-v3.schema.json) schemas are checked in. Semantic restrictions are enforced by the static runtime.

## Contract and security decisions

- Bundle schema: **`aiab-baked-plan/v1`**. The old registered-JSON plan also used
  `aiab-plan/v1` but had different fields; new files deliberately have a distinct name.
- Report: **`aiab-report/v3`**, adding `aiab_id`, `image_digest`, `egress_max_bits`.
  Existing v1/v2 verifiers and archived evidence remain unchanged.
- `plan_digest`: SHA-256 over `AIAB/folder/v1\0`, then each sorted UTF-8 path and raw
  content, framed by 4-byte path length and 8-byte content length (big endian).
  Included: `__init__.py`, `plan.py`, `plan.json`, `prompts/*.txt`.
  Tests, signatures, READMEs and historical JSON examples are excluded and never
  executed by the loader. Changing whitespace in an included file changes the hash.
- Each Ed25519 signature covers the existing domain-separated canonical message
  `AIAB/plan-bundle/v1\0` + RFC8785 `{"plan_digest":"sha256:..."}`.
  Party/logger public keys and exact model binding live in the static reviewed
  `static/aiab/baked/trust.json`, not in caller-selected trust roots.
- Docker copies only the included plan files and two signatures. CI, Docker build
  and boot independently verify both signatures and the static restrictions.
  The plan never contains an image digest, avoiding a circular commitment.
- Every run starts a fresh Linux x86_64 subprocess with no credentials in its
  environment, no inherited service sockets, no report key, no other run's data,
  resource limits, and a seccomp syscall allowlist installed before plan code runs.
  File opens, sockets, process creation, exec and cross-process memory access are
  blocked. The source is normal compiled Python, not the older expression interpreter.
  Static import checks admit only named API symbols published in manifest restrictions; whole modules and internal helpers are rejected. These checks are an additional restriction, not the isolation boundary.
- The parent broker validates every query against authenticated input and limits,
  preflights every complete query's tokenizer count before the first inference,
  enforces prompt/model/call order, validates output, and signs the report. Worker
  stdout is bounded protocol input; worker stderr is discarded. A failed sandbox
  startup fails the run; it never falls back to in-process execution.
- Bounded output schemas include booleans, integer ranges, string enums/bounded
  strings, bounded arrays and closed objects. No remote references. The reported
  bit bound is conservative and covers only the result, not metadata/timing/retries.
- All service state remains per boot. Restart loses reports and changes report key;
  archive reports/identity before update. Worker exit is not a verified memory wipe.

## Review, evaluate and sign

From `static/`, install dependencies with `uv sync --locked`.

```sh
# Explicit development-only fake inference; never accepted as final evaluation.
uv run python -m aiab.dryrun ../plan --mock --output /tmp/mock-evaluation.json

# Real, pinned confidential inference, when this host can reach the pinned model.
uv run python -m aiab.dryrun ../plan \
  --credential-file /absolute/private/inference.key \
  --output /tmp/real-evaluation.json

# Commit real metrics into an UNSIGNED replacement plan.json.
uv run python -m auditor.baked --folder ../plan commit-evaluation \
  --evaluation /tmp/real-evaluation.json --output /tmp/final-plan.json
```

Review `/tmp/final-plan.json`, replace `plan/plan.json` with it, then both parties
independently sign that exact bundle. Evaluation data and results must accompany
review: a signed hash alone is not evidence of accuracy. The demo's three samples
are a smoke test. If local networking cannot reach the inference enclave, deploy
an explicitly signed evaluation candidate, run the synthetic acceptance driver on
that actual service, then use its `evaluation` artifact to prepare/re-sign/redeploy
the final bundle. A candidate with zero evaluation commitments is not a final
agreement with measured FP/FN. `commit-evaluation` rejects mock evaluation records.

```sh
uv run python -m auditor.baked --folder ../plan digest
uv run python -m auditor.baked --folder ../plan sign \
  --role lab --private /absolute/private/lab.key --output /tmp/lab.sig
uv run python -m auditor.baked --folder ../plan sign \
  --role auditor --private /absolute/private/auditor.key --output /tmp/auditor.sig
```

Put the reviewed signature files at `plan/signatures/lab.sig` and `auditor.sig`.
The signing commands refuse to overwrite outputs. Never commit private keys.

```sh
uv run python -m aiab.baked.check ../plan
make check
```

The demo uses the existing two locally provisioned test-role keys; it does not
establish independent institutional approval. Production parties keep their own keys.

## Build, approval and deployment

`.github/workflows/approve-plan.yml` uses only the trusted base revision's verifier
and keys. It fetches candidate files as bounded data, never checks out or executes
PR Python, rejects any change outside `plan/`, verifies signatures/restrictions and
merges only the checked head. Repository protection still applies; no admin bypass.
It explicitly dispatches the build because GitHub's `GITHUB_TOKEN` merge events do
not automatically trigger push workflows ([GitHub documentation](https://docs.github.com/en/actions/concepts/security/github_token)).
This workflow becomes active only after the initial static implementation is merged
into the target branch. Protected-branch checks must permit the separate approval
workflow to merge; static-code changes require ordinary review and are never
eligible for automatic plan-only approval.

The build workflow tests, verifies the plan, builds twice from clean inputs,
compares image digests and publishes `audit-build.json` with **image and plan
commitments**. It runs on GitHub-hosted runners, never inside the enclave.
The external release coordinator dispatches CI measurement, updates the Tinfoil
instance, verifies the expected policy/attestation and runs real synthetic acceptance:

```sh
PYTHONPATH=static static/.venv/bin/python static/deploy/tinfoil/audit/release.py \
  --source-ref feat/baked-plan-runtime --tag v0.2.0 \
  --key-directory /home/jon/.local/share/aiab-audit-demo \
  --output /tmp/new-release-attempt --execute
```

Run from a clean pushed checkout. Use a fresh release tag and output directory.
This is the same supported outside-enclave update mechanism as v0.1.x. No provider
admin credential is installed in CI. The operator command automates the deployment
once invoked; it is not a continuously running auto-deploy daemon.

## Submit input and get a report

Service address remains `https://aiab-signed-audit.lucid-computing.containers.tinfoil.dev`.
Use the checked-in deployment `release.json` for the actual current tag and pins.
`POST /aiab/plans` is disabled for the baked service; changing an agreement requires
a newly signed bundle and image.

`POST /aiab/runs` accepts `submission_id`, baked `plan_digest`, `input_scope`,
`records` and `logger_evidence`. Record shape is fixed in
`static/aiab/baked/records.py`. The source signature covers the ordered canonical
record bytes, sequence/hash chain and scope. Record timestamps/agents are checked
against the submitted and agreed windows; duplicate source positions are rejected.
Integrity is checked before filtering. Admission rejects bad scope, byte limits and record counts synchronously. Tokenizer checks run in the worker preflight after the 202 response but before any inference; an oversized assembled query produces a signed `input_too_large` failure. Submission idempotency is scoped to the authenticated role and current boot. This **synthetic export protocol is not
native WORM verification**; real logger fixtures and its transformation are still needed.

Prepare an explicitly synthetic, genuinely signed request:

```sh
uv run python -m auditor.baked --folder ../plan mock-export \
  --records ../plan/tests/transcripts/benign_coding.jsonl \
  --private /home/jon/.local/share/aiab-audit-demo/logger.key \
  --submission-id demo-unique-001 --output /tmp/submission.json

uv run python -m auditor.baked --folder ../plan submit \
  --release deploy/tinfoil/audit/release.json \
  --token-file /home/jon/.local/share/aiab-audit-demo/lab.token \
  --input /tmp/submission.json --output /tmp/admission.json
```

The client verifies attestation, release, effective manifest, image and baked-plan
commitments before sending private data. Submission returns a run ID. The CLI independently computes and saves
`input_commitment` in its admission output. Use those two fields to retrieve:

```sh
uv run python -m auditor.baked --folder ../plan report \
  --release deploy/tinfoil/audit/release.json \
  --token-file /home/jon/.local/share/aiab-audit-demo/lab.token \
  --run-id RUN_ID --input-commitment sha256:EXPECTED \
  --output /tmp/report.json
```

The CLI polls `GET /aiab/runs/{run_id}/report` and verifies its signature and all
bindings. Lab or auditor can retrieve; lab or logger can submit. Failed runs return
signed fixed error codes, never raw exception/model text. Report reads do not rerun.

For offline verification, use `pin-identity` with the same release/token arguments
to save a trusted anchor while connected to the attested service. Then:

```sh
uv run python -m auditor.baked --folder ../plan verify-offline \
  --release deploy/tinfoil/audit/release.json --anchor /tmp/anchor.json \
  --report /tmp/report.json --run-id RUN_ID --input-commitment sha256:EXPECTED
```

This command makes no network call. The anchor must have been independently trusted;
an arbitrary bundle-supplied key is not a hardware trust root. Preserve the matching
released plan folder and release file for historical reports after upgrades.

## Explicit remaining dependencies

Native logger verification, portable hardware endorsement of the report key,
public source licensing, the separate UI and broader post-demo requirements remain
open. The current CPU host is AMD SEV-SNP, not Intel TDX specifically. No claim of
verified erase, durable appeal state or lifetime disclosure budget is made.
