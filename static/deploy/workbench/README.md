# Confidential workbench supervisor and independent collector

This is a concrete factory/collector integration, **not a deployed or qualified
confidential service**. The existing Render UI remains separate and must continue
blocking confidential admission. Nothing here deploys, changes a control plane,
starts a development server, or accepts plaintext evidence through that UI.

Reproduce the controlled **synthetic, test-attestation** demonstration:

```sh
uv sync --project static --locked
static/.venv/bin/python static/deploy/workbench/synthetic_demo.py
static/.venv/bin/pytest -q static/deploy/workbench/tests \
  static/tests/workbench/test_runtime_service.py static/tests/test_workbench_governance.py
```

The demo uses real RFC8785 commitments, Ed25519 consent/receipts, X25519/HKDF/AES-GCM
sealed inputs, encrypted durable SQLite records, actual contracts/evidence/runner,
and real aggregate recomputation. Its transport is an explicitly named in-process
TEST seam. It performs **no hardware attestation or inference**. Generated keys,
identities, grants, roster, approvals and records are all labelled TEST and exist
only in its temporary directory. No synthetic keys belong in an operator release.
The production factory and CLI reject test profiles; neither exposes an HTTP test
switch. Production execution/transport injection is rejected.

## Trust and deployment gates

An independently approved public Ed25519 root is baked into the measured image at
`/app/deploy/authority.pub`. Operators supply that public file before building;
this repository deliberately includes no default root or signing key. The running
factory cannot choose another root through environment/request JSON. Collectors
pin the root independently on employee-controlled machines. Identity enrollment,
roles, complete rosters, source grants, exact plans, release policies, collector
keys and the entire deployment profile are signed by that root; source grants also
require a **separate enrolled source authority's signature**, never the owner.
A root signature establishes the authorized issuer, not the truth of its assertions.
Independent identity proof, voluntary consent, anti-coercion, directory completeness,
source authenticity/permission review and signed coverage assertions remain real
operational responsibilities.

Before issuing a `signed-deployment-gate` profile, the independent authority must
review evidence for all six gates. Live gate values must be `sha256:<64 hex>`
commitments to the actual review artifacts, not booleans:

- `measured_image`: measured runtime release/config, its exact image and locked
  Node/Pi/Python/native sandbox/tool code, boot settings and update root.
- `protected_storage`: input/result/receipt key provisioning, encrypted persistent
  `/private` volume **inside** the confidential boundary, backups/WAL, retention,
  independent ledger checkpoint witness and rollback detection.
- `no_ui_plaintext`: direct collector-to-runtime ingestion and minimal recipient
  output; no evidence, key, private trace, raw debugging or telemetry at the UI host.
- `sandbox_review`: Linux x86_64 native Pi/SQL/Python isolation, process/egress
  policy, supervisor key isolation, no core dumps, resource limits, threat review.
- `collector_review`: reviewed employee-controlled collector/update chain and
  protected local principal/collector keys; actual pinned runtime attestation.
- `source_authority_review`: independent fixed population/privacy domain, exact
  source/time/field permissions, original record/snapshot authenticity,
  per-subject universe/coverage and revocation propagation.

These are signed deployment approval records, **not live hardware measurements**.
The independently trusted collector MUST additionally use
`aiab.audit.transport.PinnedConnection(host, repo, release_digest)` to verify the
actual running runtime before sending credentials, approvals or sealed evidence.
It explicitly pins all three, checks the attested connection before/after requests,
fetches and verifies the profile/key binding over that channel, and signs a short
lived ticket bound to principal, request, exact plan, exact normalized input,
profile and X25519 key. A public key from the UI or ordinary HTTPS is insufficient.
The factory rejects absent/expired profiles, mismatched keys, unapproved collectors
and unsigned or changed tickets; it constructs GovernanceStore compatibility facts
only after those independent checks. HTTP callers cannot supply runtime-facts.
Profiles and source permissions expire within 24 hours; tickets expire within ten
minutes. Expiry during execution/release denies output; no silent renewal.

**External gates still blocked:** no image was built/published, measured release
approved, protected volume/key service provisioned, or live collector attestation
performed by this work. DeepSeek remains exclusively the existing reviewed Tinfoil
profile. Known MR_SEAM rejection remains a failure, with no SDK allowlist relaxation,
fallback provider, alternate model or inference retry. Real multi-turn Pi inference
and independent security review are still required. The local demo proves neither.

The existing runner exposes private event checkpoints, not a mandatory hook before
every tool access/inference. This adapter revalidates before/after execution, every
available event, and release. Revocation blocks release immediately once committed;
it cannot recall data already disclosed to the runtime/model or guarantee zero
additional reads during a pending step. **Do not approve a live deployment claiming
instantaneous per-access revocation until the runner supplies reviewed pre-access
and pre-inference hooks.** Signed gates cannot manufacture those missing semantics.

## Measured-image packaging (parent-owned build/deploy)

```sh
# Install independently reviewed PUBLIC authority.pub in this directory.
# Supply full repository@sha256:... references, never mutable image tags.
NODE_IMAGE='docker.io/library/node@sha256:<reviewed digest>' \
PYTHON_IMAGE='docker.io/library/python@sha256:<reviewed digest>' \
UV_IMAGE='docker.io/astral/uv@sha256:<reviewed digest>' \
  static/deploy/workbench/build.sh
```

The Node image must be **22.22.2 bookworm slim**, Python must be 3.12 bookworm,
and uv must be 0.11.6. The image runs `npm ci --ignore-scripts` from the existing
Pi 1.5.0 package lock, compiles `isolate.node` using matching Node headers, and
runs `uv sync --locked --no-dev` against `static/uv.lock`. Build context excludes
local environments, node_modules, SQLite and private key files. The final image
has one fixed Uvicorn worker, no access logs/docs/debug route and a `/readyz` check.
Base pins and resulting image/release digest must be recorded by the parent.
APT build dependencies are resolved at build time; the resulting image is measured,
not asserted to be bit-for-bit reproducible from an unpinned Debian mirror.

Provision these files only inside the protected supervisor boundary, readable by
UID 10001, without placing their bytes in environment variables or Pi messages:

| Path | Bytes |
| --- | --- |
| `/run/aiab-private/deployment.json` | Signed deployment document below |
| `/run/aiab-private/input.key` | Raw 32-byte X25519 private key |
| `/run/aiab-private/result.key` | Random 32-byte AES-256 key, stable across authorized restarts |
| `/run/aiab-private/receipt.key` | Raw 32-byte Ed25519 receipt private key |
| `/private` | Durable protected local POSIX filesystem; same ledger across accounts/restarts |

`factory()` is the only image entrypoint. No worker-count override or test flag.
Run read-only rootfs/no-new-privileges, disable host core/log collection, constrain
network to required verified inference and attestation services, and enforce key
release against the reviewed measurement. Container UID alone is not a TEE.
The receipt key is externally provisioned to the supervisor and overrides the
GovernanceStore signer; it is never sent to Pi. The base GovernanceStore still
creates an unused internal key record; that record is not the receipt trust root.

## Signed configuration format

Every signed document is `{payload,signature}`. `signature` is base64 Ed25519 over
`governance.canonical(payload)` (RFC8785); keys are base64 raw 32-byte public keys.
The profile payload has these fields; see the completely executable labelled
fixture in `tests/test_confidential.py::synthetic_fixture` for exact shapes:

- `domain: "aiab-confidential/deployment/v1"`,
  `assurance: "signed-deployment-gate"`, numeric Unix `expires_at`.
- Exact runtime `host`, `repo`, `release_digest: "sha256:..."`, six `gates`,
  exact `model_profile` from `aiab.workbench.model.MODEL_PROFILE`,
  `input_public_key`, `receipt_public_key`.
- `identities: [{id,public_key,roles,capabilities}]`; roles are GovernanceStore
  authority roles. Capabilities are separate `runtime:submit/status/cancel/release`
  strings. The principal token is signed locally with an enrolled identity key,
  binds the complete profile digest as audience and expires after five minutes.
  UI sessions, caller role strings and self-enrollment confer no authority.
- `collectors: [identity_id,...]`, independently provisioned.
- `rosters: [{roster_id,subjects,actor,source_commitment,scope_id}]` matching
  `freeze_roster`; `policies` matching `configure_release_policy` arguments.
- `plans: [{plan,owner,roster_id,source_authority,source_grant,dataset_manifest,authorization}]`.
  The source grant payload has `domain: "aiab-confidential/source/v1"`,
  `plan_digest`, `scope`, `dataset_manifest`, `authorization`, numeric `expires_at`.
  It binds every normalized input byte, source manifest and aggregate method.
  `authorization` uses the exact GovernanceStore source authorization schema.

Immutable identities/rosters/policies/plans/collectors are frozen in the persistent
ledger; a changed signed profile cannot replace them or reset privacy budgets.
Renewal can change deployment expiry/gate evidence but cannot rewrite the embedded
source grants. Renewing an expired exact source binding requires a separately
reviewed migration and renewed human signatures; no migration bypass is provided.
Do not reinitialize the volume to change bindings or bypass a refusal/budget.

The approved `dataset_manifest` is produced by
`input_manifest(dataset, aggregate_rule, subject_coverage)` and includes exact
`dataset_digest`, dataset-native manifest, method and subject universe.
`source_permissions.scope` must exactly equal the explicit plan scope. Unknown
actors, sources, time violations, lossy events, wrong cardinality, duplicate native
identities or changed bytes are denied rather than silently attributed/filtered.
Source coverage must list the exact sorted event IDs for every authorized subject
and explicitly declare `complete: true|false`. Signed coverage is an authoritative
collection assertion, not cryptographic proof that all real events were collected.

## Aggregate release semantics

Generic model findings cannot independently prove one classification per subject.
This adapter deliberately supports one concrete approved aggregate rule:

```json
{"version":"aiab-confidential/event-boundary/v1","event_type":"transfer",
 "classification":"confidential","forbidden_destinations":["TEST-forbidden"]}
```

This example is **synthetic**, not a real source policy. For each exact authorized
subject, the rule finds events with the exact event type/classification and an
`attributes.destination` in the approved list. At least one match gives `supported`;
no match with an authority-declared complete observed universe gives `no_finding`;
otherwise `insufficient_evidence`. This is a deterministic predicate over authorized
records, not a conclusion about intent or arbitrary model claims. The rule and
coverage are committed in the human-approved input binding.

The real runner executes the approved graph privately. Its model prose, findings,
trace and arbitrary details are never used as aggregate values. The supervisor
constructs typed `{subject,outcome,evidence_ids,universe_ids}` records from input;
release independently decrypts/rechecks that exact input and recomputes the entire
result. Exact population, one record per subject, source/time permissions, event
provenance, all universe IDs, supporting IDs and outcomes must match. Same length
with duplicate/misassigned subjects or changed outcomes fails. No arbitrary finding
field is treated as an attribution channel. Generic/other aggregate methods require
a new reviewed validator; they are not implicitly supported.

GovernanceStore then rechecks live consent/source permissions and fixed query
family, reserves the global privacy-domain budget, suppresses all small-cell counts
and complements, constructs only approved `counts/status/coverage`, and signs the
receipt. The independently authenticated recipient need not be the owner, but
receives only its own package. Owner/status access does not authorize release to
an unapproved account. Private inputs/results are AES-GCM encrypted at rest with
run-ID AAD; plaintext exists only within the confidential supervisor boundary.
Counts/coverage prove the specified computation over approved submitted bytes,
not factual correctness, source completeness or differential privacy.

## Independent employee/collector workflow

Review the entire signed plan, source grant, input method/universe, recipients,
roster, expiration and profile on an employee-controlled machine. Keep local keys
outside UI infrastructure. The CLI never creates a human signature automatically:

```sh
# Locally derive the exact GovernanceStore payload; review it and the agreement.
static/.venv/bin/python -m aiab.workbench.confidential_client approval-payload \
  --bundle reviewed-deployment.json --authority-key pinned-authority.pub \
  --plan-digest "$PLAN_DIGEST" --actor "$EMPLOYEE_ID" \
  --expires-at 2026-11-06T00:00:00Z > reviewed-approval.json
# Explicit human signing step, on their own machine/key.
static/.venv/bin/python -m aiab.workbench.confidential_client sign-consent \
  reviewed-approval.json --actor "$EMPLOYEE_ID" --key employee-private.key \
  --output employee-approval.json
# Independently collect all required actual signatures in approvals.json (a JSON array).
static/.venv/bin/python -m aiab.workbench.confidential_client collect \
  --bundle reviewed-deployment.json --authority-key pinned-authority.pub \
  --plan-digest "$PLAN_DIGEST" --approvals approvals.json --dataset private-snapshot.json \
  --host "$APPROVED_RUNTIME_HOST" --repo "$APPROVED_RUNTIME_REPO" \
  --release-digest "$APPROVED_RUNTIME_RELEASE" --principal "$OWNER_ID" \
  --principal-key owner-private.key --collector "$COLLECTOR_ID" \
  --collector-key collector-private.key > admitted.json
```

Collector verifies every required exact human signature and the independent source
permission signature locally before connecting. It submits those real approvals
through the verified runtime transport, then sends only a sealed payload. Missing,
stale, changed or replayed approval fails. If approvals have **already** been
accepted, `--consent-already-submitted` avoids re-sending them; local verification
and runtime current/revocation checks still apply. A local approval bundle is not
proof that an approval remains unrevoked at runtime.

`admitted.json` includes runtime `run_id`, `governance_run_id`, exact `plan_digest`,
`binding_digest`, `profile_digest` and assurance label. `status` and `release` CLI
subcommands require the same bundle/root/host/repo/release/principal/key pins plus
`--run-id`. `release` additionally requires the admission's `--governance-run-id`
and `--binding-digest`, verifies the signed package against the pinned receipt key
and exact plan/binding/recipient/run, and prints only that recipient-safe response.
There is no unverified TLS or alternate-provider CLI option.

`verify-receipt package.json --public-key pinned-receipt.pub --plan-digest ...
--binding-digest ... --recipient ... --run-id <governance_run_id>` verifies offline;
provide the inner `result` object (`{projection,receipt}`), not the HTTP wrapper.
Receipt signature/bytes/bindings are distinct from factual/runtime assurance.

`sign-revocation --key ... --actor ... --plan-digest ... --profile-digest ...
--output revocation.json` creates a locally signed monotonic withdrawal; `--source`
requires an enrolled independent source authority and invalidates the entire source
grant. Submit the document to `POST /v1/consent` over a pinned verified connection
(no UI evidence upload). Replayed withdrawal can only keep access revoked.
Actual consent signature replay is rejected. This endpoint cannot enroll keys,
replace rosters, provision grants/policies or supply runtime facts.

There are no raw result, evidence, private trace, OpenAPI, interactive docs, UI proxy
or debug endpoints. `/readyz` checks freshness and ledger integrity; its `ready`
means local readiness under signed policy, **not a successful hardware verification
or live inference qualification**. `/v1/deployment` exposes only public key/profile
bindings. SQLite metadata remains private supervisor state. Export checkpoints to
an independent witness; same-disk hash chains cannot stop rollback by themselves.
