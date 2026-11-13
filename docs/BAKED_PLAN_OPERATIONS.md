# Folder-based audit operations

Current deployed release: **v0.2.2**. [Deployment pins](../static/deploy/tinfoil/audit/release.json).

The runtime runs jointly signed plan bundles, one sent with each run (see
[Send the plan with the run](#send-the-plan-with-the-run)); the image holds no plan.
`plan/` in this repository is the example folder the commands below use; the UI keeps its
agreements under `agreements/`. The plan is a graph (`plan.json`, format `aiab-plan-ir/v0`):
the logs of a run, filters and AND/OR joins of those logs, Python steps, model steps, a
classifier and the final report, run in
dependency order by `static/aiab/baked/graph.py` inside the sandboxed worker, with every
model call made by the parent broker. The plan is written in the agreement UI's builder,
which applies the same checks (`plan_ir.py`). The native logger is a separate workstream. The broader mandatory requirements are tracked in
[REQUIREMENTS_COMPLIANCE.md](REQUIREMENTS_COMPLIANCE.md).

The code is the schema: `static/aiab/baked/plan_ir.py` defines the plan, and `records.py` and `runtime.py` in the same folder define the submission, the logger evidence and the report. (The JSON schema snapshots under `schemas/` were removed on 3 Nov 2026.) Semantic restrictions are enforced by the static runtime.

## Contract and security decisions

- Plan format: **`aiab-plan-ir/v0`** (`static/aiab/baked/plan_ir.py`). The earlier
  `aiab-baked-plan/v1` bundles (plan.py and prompts) no longer load; the UI archives them.
- Record steps: a Filter (`filter_logs`) keeps the records that match its rules (text,
  record kind, agent, session, time, length; all or any; `exclude` drops them instead), or
  asks the model one yes/no question per record or per session, as a classifier with the
  labels yes and no. AND / OR (`combine_logs`) keeps the records in both inputs or in either,
  by record id, once each, in the order of the run's records. Each hands on only records it
  was given. They were added to `v0` without a new name: every plan that loaded before still
  loads and means the same, and an older box refuses a plan that uses them. A "matches
  regex" rule accepts a small subset (`static/aiab/baked/pattern.py`, the same check in the
  builder) that refuses back-references, look-around and nested repeats; matching runs on
  Python's backtracking engine, so a slow pattern over long records is stopped only by the
  worker's CPU limit (`execution_timeout`), as plan code is.
- Report: **`aiab-report/v3`**, adding `aiab_id`, `image_digest`, `egress_max_bits`.
  Existing v1/v2 verifiers and archived evidence remain unchanged.
- `plan_digest`: SHA-256 over `AIAB/folder/v1\0`, then each sorted UTF-8 path and raw
  content, framed by 4-byte path length and 8-byte content length (big endian).
  Included: `plan.json` alone.
  Tests, signatures, READMEs and historical JSON examples are excluded and never
  executed by the loader. Changing whitespace in an included file changes the hash.
- Each Ed25519 signature covers the existing domain-separated canonical message
  `AIAB/plan-bundle/v1\0` + RFC8785 `{"plan_digest":"sha256:..."}`.
  Party/logger public keys and exact model binding live in the static reviewed
  `static/aiab/baked/trust.json`, not in caller-selected trust roots.
- The image holds the runtime, the trust roots (`static/aiab/baked/trust.json`) and the
  model pin, never a plan. A plan sent with a run is verified inside the measured image
  against those trust roots, for that run only; the box stores no plan. The manifest
  (and so `policy_digest`) names no plan. Each signed report carries the `plan_digest`
  it ran, which is what a verifier checks against the plan it signed. The plan never
  contains an image digest, avoiding a circular commitment.
- Every run starts a fresh Linux x86_64 subprocess with no credentials in its
  environment, no inherited service sockets, no report key, no other run's data, a
  memory ceiling of three quarters of the container's memory, an hour of CPU time for plan
  code (waiting for the model does not count), and a seccomp syscall allowlist installed
  before plan code runs.
  File opens, sockets, process creation, exec and cross-process memory access are
  blocked. The source is normal compiled Python, not the older expression interpreter.
  Python steps may import only re, math, collections, itertools, statistics and json; reflective builtins, private attributes and classes are refused by a static check. These checks are an additional restriction, not the isolation boundary.
- The parent broker builds every prompt itself from the signed plan (its framing first),
  serves each model node once, preflights every prompt of a node against the model's
  context before that node's first inference, validates every answer against the schema it
  dictated, validates the result, and signs the report. Worker stderr is discarded.
- A run has no limit on records, bytes, model calls or time. Plans signed with `limits` or
  `max_records` still load; those fields are ignored. An item longer than the model's
  context is split into pieces that fit (never cut) and the answers are merged
  (`static/aiab/baked/graph.py`). The only size cap left is the report's
  `privacy.max_result_bytes`, which bounds what the report discloses. Runs beyond four at
  once wait for a slot; the box keeps the last 128 runs and drops the oldest finished one
  first. A failed sandbox
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
```

Review the evaluation, then both parties independently sign that exact bundle. Evaluation
data and results must accompany review: a signed hash alone is not evidence of accuracy.
The mock model is a development stand-in; `--mock` runs are never evidence.

```sh
uv run python -m auditor.baked --folder ../plan digest
uv run python -m auditor.baked --folder ../plan sign \
  --role lab --private /absolute/private/lab.key --certificate /absolute/lab.cert --output /tmp/lab.sig
uv run python -m auditor.baked --folder ../plan sign \
  --role auditor --private /absolute/private/auditor.key --certificate /absolute/auditor.cert --output /tmp/auditor.sig
```

`--certificate` is the certificate the party's root made for the signing key
(`ui/assets/sign_digest.py certify`, or `make dev-keys` for development keys); `sign`
checks it against the root in the trust file and writes it next to the signature as
`/tmp/lab.cert`. Put the reviewed files at `plan/signatures/lab.sig`, `lab.cert`,
`auditor.sig` and `auditor.cert`. The signing commands refuse to overwrite outputs.
Never commit private keys.

```sh
uv run python -m aiab.baked.check ../plan
make check
```

The demo uses the existing two locally provisioned test-role keys; it does not
establish independent institutional approval. Production parties keep their own keys.

## Build and deployment

The image changes only when the static part or a party's key changes, never for a plan.
`.github/workflows/approve-keys.yml` merges a well-formed key change to
`static/aiab/baked/trust.json` and starts the release, since a key reaches the box only
inside a new measured image. Static-code changes require ordinary review.

The build workflow builds twice from clean inputs, compares image digests and publishes
`audit-build.json` with the **image commitment**. It runs on GitHub-hosted runners,
never inside the enclave. The release coordinator dispatches CI measurement, updates the
Tinfoil instance and verifies the expected policy/attestation and health:

```sh
PYTHONPATH=static static/.venv/bin/python static/deploy/tinfoil/audit/release.py \
  --source-ref dev --tag v0.2.0 \
  --key-directory /home/jon/.local/share/aiab-audit-demo \
  --output /tmp/new-release-attempt --execute
```

Run from a clean pushed checkout. Use a fresh release tag and output directory.
`release.yml` runs the same procedure on a push to `production`, `staging` or `dev` that
touches `static/` or `.dockerignore`. No provider admin credential is installed in CI.

## Submit input and get a report

Service address remains `https://aiab-signed-audit.lucid-computing.containers.tinfoil.dev`.
Use the checked-in deployment `release.json` for the actual current tag and pins.
A new agreement is a newly signed bundle, sent with each run. `POST /aiab/plans` is not
served (405 `plan_in_submission`).

The plan's `input_scope` block is optional (as is `filter`). Without it the box checks each
record only against the submission's own scope, which the logger derives from the records;
with it, the submission's scope must also lie inside the plan's.

`POST /aiab/runs` accepts `submission_id`, `plan_digest`, `input_scope`, the signed `plan`,
`records` and `logger_evidence`. Record shape is fixed in
`static/aiab/baked/records.py`. The source signature covers the ordered canonical
record bytes, sequence/hash chain and scope. Record timestamps/agents are checked
against the submitted and agreed windows; duplicate source positions are rejected.
Integrity is checked before filtering. Admission rejects bad scope and malformed records synchronously; there is no byte or record-count limit. Context checks run in the broker after the 202 response but before any inference; a model question that cannot be split to fit (a Value step's single call, or a prompt too long by itself) produces a signed `input_too_large` failure. Submission idempotency is scoped to the authenticated role and current boot. This **synthetic export protocol is not
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

The client verifies attestation, release, effective manifest and image commitments
before sending private data, and attaches the signed plan folder to the submission. Submission returns a run ID. The CLI independently computes and saves
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

## Send the plan with the run

`submit` attaches the signed folder to the submission as
`plan: {"files": {"plan.json": text}, "signatures": {"lab": ..., "auditor": ...}, "certificates": {"lab": {...}, "auditor": {...}}}`,
over the verified connection. The box recomputes the digest over the UTF-8 bytes of the
text, checks each certificate against the party's baked root and the signature against
the certified key, applies the static restrictions,
checks the digest equals the submission's `plan_digest`, runs the plan once on the
records, signs the report with that digest, and keeps nothing of the plan. A wrong
or tampered plan fails in admission; no run starts. Lab or logger may submit, as before.
A submission without `plan` is `invalid_submission`.

A signed bundle is not single-use: the box keeps nothing, but the file plus both
signatures remain a valid plan for any later submission with other records. If one
signing should authorise one run, the parties sign a run identifier inside `plan.json`
and the box must check it against the submission; this is not implemented.

What this changes in the guarantee: a third party still learns the code from the image
digest, and learns which plan produced a report from the report's signed `plan_digest`,
rather than from the image digest alone. The signature check in admission is part of the
measured code and is covered by `static/tests/test_submitted_plans.py`.

## Explicit remaining dependencies

Native logger verification, portable hardware endorsement of the report key,
public source licensing, the separate UI and broader post-demo requirements remain
open. The current CPU host is AMD SEV-SNP, not Intel TDX specifically. No claim of
verified erase, durable appeal state or lifetime disclosure budget is made.
