# Confidential workbench runtime

**DeepSeek transport update:** An opt-in official Go v0.16.4 verified stdio broker
is now available. See [build and qualification instructions](verified_transport/README.md)
and [the source/live review](review/VERIFIED_TRANSPORT_REVIEW.md). The historical
Python-only failure discussion below does not describe the new Go transport.
The current [live qualification artifact](review/live-qualification.json) records
actual results; unit doubles remain separate.

This is a new runtime path. The legacy signed-plan worker, its seccomp allowlist,
its model bindings and its Python dependency lock are unchanged. Do not put Node
inside that worker or relax its sandbox. Run this service **inside the measured
confidential execution boundary**, with private evidence and its broker in that
boundary. A normal host serving this code is not confidential computing.

## Build and integration

The existing static Python environment supplies FastAPI, cryptography, rfc8785,
Tinfoil 0.14.0 and the installed `aiab` package. No new Python dependency is needed.
Linux x86_64, Node 22.22+, a C compiler and Node N-API headers are build requirements:

```sh
npm ci --prefix static/agent_runtime --ignore-scripts
make -C static/agent_runtime
npm test --prefix static/agent_runtime
static/.venv/bin/pytest -q static/tests/workbench
```

`package-lock.json` freezes the Pi 1.5.0 packages and their dependencies. Build
`isolate.node` in the measured image; include the `agent_runtime` directory next to
`aiab`, including installed node_modules. Do not run npm/compiler in a live audit.
The parent owns image builds, measured release/attestation, mounting operator
credentials, exposing HTTPS, service admission adapters and deployment. No server,
container, deployment, PR or push was created by this workstream.

`await runner.execute_plan(plan, dataset, *, model=None, actor=None, on_event=None, authorize=None)`
returns the PRIVATE result. Cancel the asyncio task to stop it. `on_event` contains
PRIVATE evidence/tool messages/reasoning; never connect it to an employer/UI event
stream. Only pass recipient-safe lifecycle status through the host API.

- Personal `actor` is the authenticated actor ID, or a trusted dict with `id`.
  Personal access is intersected with that ID, including when scope is unspecified.
- Joint/confidential `actor` must be a trusted admission dict with `id`,
  `authorized_dataset_ids` and `authorized_subjects`. Never accept these fields as
  caller assertions. Unknown actors are excluded, not attributed by inference.
- The plan must explicitly name the dataset. The evidence store then enforces
  subject, time and field selection. Step input selections further narrow tools.
- `contracts` / `evidence` / `analyzers` are lazy integration dependencies owned by
  the other workstreams. Production never substitutes their test fixtures.
- A test model requires `is_test_model=True`, async `start`, `count_tokens(messages,
  tools)`, `complete(messages, tools, max_tokens)` returning `{message,usage}`,
  and `close`. Injection is marked `usage.test_model`. HTTP requests cannot inject
  models. Tests exercising this seam are not live-inference evidence.

Every live agent step must contain `config.model_profile` equal to the entire
`aiab.workbench.model.MODEL_PROFILE` dictionary. Freeze this field **before plan
approval**. A model name is insufficient. Optional `config.instructions` refines the
step; it cannot change authority, provider, tools, release, or budgets. Runtime and
receipt identities must also bind the harness, lockfile, tool code and sandbox.
The runtime does not sign receipts or own approval keys.

The operator selects `AIAB_DEEPSEEK_HOST` explicitly and supplies `TINFOIL_API_KEY`
in the confidential supervisor environment. There is no generic base URL, router
inference, alternative provider, fallback model or automatic inference retry.
Wrong release, changed attested key, failed tokenizer, context overflow, output
truncation and usage beyond reservation all fail closed. Default budgets are 12
turns, 32,000 **cumulative prompt plus completion** tokens, 40 tool calls, and 180
seconds. Absolute ceilings are 64/262,144/256/900. Money cost is `null` because no
contracted price schedule has been approved; it is never reported as zero.

## Tools, graph and findings

Pi core runs a real adaptive multi-turn `Agent` with an explicit custom stream
function. No coding-agent CLI, provider registration, session persistence, project
configuration loader, default tools, shell, skills or agent-generated JS execution
is used. The broker buffers each verified completion and translates it into Pi's
completion event protocol; network-token streaming is intentionally not supported.
DeepSeek `reasoning_content` is preserved privately on tool rounds.

Tools have one schema field, `request`, a JSON-encoded object. Operations:

- `search`, `counterevidence`: `{query, filters?}` through scoped EvidenceStore.
- `fetch`: `{ids}`; unauthorized/missing IDs fail, and only fetched content counts
  as examined support. A search hit alone is not counted as full examination.
- `join`: `{left_ids,right_ids,on,right_on?,within_seconds?}`. Exact keys and
  optional absolute temporal window; null keys never match. Unknown timestamps
  fail when a temporal comparison is requested. Candidate pairs are bounded.
- `sql`: `{sql}` over an isolated in-memory SQLite `events` table containing only
  selected authorized events. Native event fields are text columns, with attributes,
  artifact_ids and provenance JSON-encoded. Returns `{columns,rows}`. Read-only
  authorizer, VM instruction bound, row/output bound, OS limits and process deadline
  apply. No attach, extension loading, filesystem tables, mutation or silent limit.
- `python`: `{ids,code}`; `events` contains exactly the fetched IDs. Assign `result`.
  json/math/statistics/collections/itertools/re/datetime (module) are preloaded.
  OS seccomp, resource limits and an external 5-second deadline apply; no files,
  subprocesses or network.
- `coverage`: `{}`; source coverage, searched/examined IDs and counter-search count.
  Explicitly does not upgrade observed records into verified complete coverage.

The disposable Python worker uses an exact `aiab-python/v1` envelope: `status:ok`
with `result`, or `status:error` with a fixed `code`. Only exact built-in syntax,
type, key, name, index, attribute, value, zero-division and assertion exceptions
from compilation/execution become correctable Python tool feedback. Messages,
tracebacks, source and exception attributes never enter that feedback. The agent
may correct its code using the existing cumulative turn/token/tool/time budgets;
each attempt is charged and authority is rechecked before returning feedback.
Graph Python errors still abort the graph. Unknown exceptions (including subclasses),
memory/recursion failures, serialization/output/input failures, bad envelopes,
nonzero exits and deadlines remain fatal. Python's seccomp denial kills the process,
so catching a forbidden filesystem/network/process syscall cannot turn it into an
analysis correction. Legacy signed-plan, SQL and rules workers retain their existing
sandbox denial behavior. Process-group cleanup remains mandatory on every exit.

Every operator config is checked before first inference. Step kinds:
`select` accepts ids OR query/filters; `rules` dispatches
`analyzers.evaluate(config.rule, selected_events, config)` in a disposable child;
`join`, `sql`, `python` use the tool configurations above (Python uses selected graph
inputs rather than an ids parameter); `aggregate` accepts count metric and optional
group_by; `assert` accepts min_events; `release` is an empty-config policy barrier.
Release never returns recipient packages itself. Events flow from select inputs;
derived table/count/finding outputs are not silently reinterpreted as source events.
A non-event input to an event operator fails. SQL/Python may analyze a select branch
while release depends on all analysis/finding branches.

Structured findings must use exactly the contract fields and outcomes. Validation
checks unique IDs, declared criteria, authorized references, fetched support for
agent citations, required support for supported/contradicted findings, and optional
`details.spans` (`event_id,start,end,text`) against exact source Unicode offsets.
Agent conclusions other than insufficient evidence require an actual counter-search;
every criterion must be addressed. This is reference validation, **not independent
semantic entailment**. The result declares semantic support and negative-claim
coverage unverified. Private traces have an RFC8785/domain-separated SHA256 chain,
a 2,000-event/16MiB cap, and are not signed receipts. Individual frames and analysis
outputs are bounded; oversized evidence produces an error, not silent truncation.

## Confidential service and sealed inputs

`service.create_app` is an ASGI application factory with mandatory injected:

```python
app = create_app(
    authenticate=authenticate_bearer,  # bearer -> Principal(id, role, capabilities)
    admit=admit_exact_plan_and_data,   # (principal, plan, dataset) -> trusted actor
    release=release_for_principal,    # (principal, governance_run_id, private_result) -> safe dict
    ledger_path="/private/runtime.sqlite",
    result_key=external_result_key,  # 32 bytes, stable across authorized restarts
    sealed_inputs=SealedInputs(external_x25519_private_key),
)
```

These are authority hooks, not optional bypasses. Admission binds exact plan/data
and must perform approval/consent checks. It can return `governance_run_id` with the
actor capability. Release must revalidate revocation and atomically enforce the
external governance ledger, field policy, recipients and repeat-release budget.
Key provisioning, attestation of this runtime and receipt signing belong outside
Pi and must be integrated by the parent. Run a single ASGI worker per ledger.

Roles owner/auditor/runtime still require each separate `runtime:submit`,
`runtime:status`, `runtime:cancel`, `runtime:release` capability. Paths:
`POST /v1/runs`, `GET /v1/runs/{id}`, `POST /v1/runs/{id}/cancel`,
`POST /v1/runs/{id}/release`, `GET /v1/input-key`. A caller cannot see another
principal's run. No raw result/trace endpoint exists. Status is minimal metadata;
release invokes the authority hook and rejects top-level private-debug fields.
The authority must also validate nested field projections and authorized excerpts.

Submit `{request_id,plan,dataset}` for authorized non-confidential use, or
`{request_id,sealed_input}`. Confidential mode requires the sealed form. The envelope
uses X25519 ephemeral agreement, HKDF-SHA256 (salt None, info
`aiab-sealed-input/v1`), AES256-GCM and a random 12-byte nonce. Its fields are
`{version:"aiab-sealed-input/v1",ephemeral_key,nonce,ciphertext}` with strict base64.
Plaintext is JSON `{plan,dataset}`. AAD is compact sorted JSON
`{"principal":"<authenticated principal>","request_id":"<unique request>"}`.
The collector must independently verify the runtime enclave and key before sealing;
a public key fetched from an untrusted UI server alone is not attestation.

The SQLite ledger atomically reserves request IDs and capacity before asynchronous
admission. Duplicate submissions fail; cancelled/failed results are discarded.
A restart marks unfinished jobs interrupted rather than replaying inference. Private
results and traces are AES-GCM encrypted with run-ID AAD; raw inputs are never
persisted here. Ledger status/owner metadata is plaintext INSIDE the confidential
boundary. Admission, disclosure accounting and retention must remain durable in
external governance. This is not a distributed scheduler, an end-to-end deployment
or a claim of immediate GPU memory erasure.

## Qualification evidence and remaining gates

`review/` retains the actual v0.0.10+b200 deployment asset, its digest and the decoded
measured config. See [RELEASE_REVIEW.md](review/RELEASE_REVIEW.md). Local tests execute
real Pi and OS sandboxes with explicitly mocked inference. They also test read-only
SQL, process timeout/cancellation, provider/identity failure, scope/reference denial,
sealed payload authentication, ownership, private result encryption and release denial.

The opt-in real test is `agent_runtime/qualify.py --transport go`. It now uses the
real Northstar evidence/contracts projection and requires all seven tools across
multiple real DeepSeek/Pi turns. The retained `review/live-qualification.json`
records a **passed** run: two turns, seven tools and 13,574 total tokens. See the
transport review for exact SDK/release/key/platform pins, tokenizer interoperability
and separate failed-attempt artifacts. Python 0.14.0 remains the default and still
rejects this B200 platform; explicitly select and prebuild the Go transport.

Remaining hard gates: measured runtime image and
client attestation/key distribution; parent-owned approval/release/receipt adapters;
independent sandbox/security and semantic-evidence review. Local unit tests do not
satisfy these live/deployment/assurance gates.

## Merged runtime integration

`execute_plan(..., authorize=check_current_authority)` accepts a separate trusted
zero-argument sync/async callback returning **exactly `True`**. A denial aborts
with `authorization_revoked`. It runs before graph steps, before every broker tool
call, before tokenization and completion, before sending responses to Pi, and at
completion. Use it for fresh approval/revocation checks; it is independent of
`on_event`, which remains a private debugging callback. The host API must wire
this hook from trusted admission state. Without the callback, the runner enforces
the admitted static capability only. Admission and recipient release checks remain
separate requirements; this hook does not authorize admission or disclosure.

Source/native validation sees the original complete snapshot before scope filtering.
Scoped graph views retain the snapshot manifest and native revisions rather than
constructing invalid partially normalized datasets. `plan_for(id)` is an authoring
template with unresolved subject/field scope; `plan_for(id, dataset=dataset)` binds
explicit observed subjects and fields for a synthetic rehearsal. Neither grants
admission capabilities. Missing grants fail closed.

Internal inventory uses `EvidenceStore.iter_event_pages()` (each page respects the
store row/byte bounds); inventory alone never counts as examination. Analysis input
uses bounded JSONL frames, at most 2 MiB per frame and 16 MiB total. Analysis output
remains 256 KiB, tool output 256 KiB, and Pi frames 2 MiB. No limit was replaced by
silent truncation. Graph event selections stay private and can exceed an individual
response frame; derived outputs still obey their output limits. Actual domain
analysis executes only in the disposable seccomp worker. Operator configuration,
graph input types and SQL semantics are checked before any inference.

Normalized tool projections exclude inline `provenance.native_bytes` even when
provenance is granted. Native payloads are available to the fixed domain analyzer
through a separate internal whole-record capability, only with all event fields
authorized, so original integrity checks still work. SQL, custom Python, Pi tools,
search and fetch see the authorized projection. Explicit native fetch additionally
requires `allow_native=True` and all fields; the Pi broker exposes no native fetch.

A direct `EvidenceStore.search` returns full projected rows and counts returned
rows as examined by default. The Pi broker explicitly requests `examine=False`:
its citation policy requires a successful fetch. Inventory, search scans, returned
hits and fetched support are distinct; failed store searches do not update coverage.
Collection coverage and native authenticity never follow from an empty search.
Synthetic negative findings retain their synthetic collector-inventory limitation.

An agent may consume a select branch, or a findings branch as investigation context.
Finding inputs authorize only their already-authorized supporting/counterevidence
IDs; findings are not source events. Additional select inputs explicitly add their
events. Prior findings enter the private prompt; the agent must still fetch its own
cited support. An agent with `inputs: []` uses the authorized run universe, as the
catalog templates specify.

Reproducible merged checks (mocked inference is explicitly labeled):

```sh
npm ci --prefix static/agent_runtime --ignore-scripts && make -C static/agent_runtime
npm test --prefix static/agent_runtime
uv run --project static pytest -q static/tests/workbench/test_runtime_integration.py
uv run --project static pytest -q static/tests/workbench static/tests/test_workbench_governance.py
```

The integration suite runs all 27 deterministic methods against 432 independently
labeled Northstar cases through `execute_plan`, and real multi-turn Pi against real
evidence/contracts with an explicit inference double. It checks scoped selections,
findings context, native revisions, byte isolation, denial/revocation, ingestion and
response budgets, deadlines, cancellation and surviving process descendants. These
are synthetic assurance checks, not successful live DeepSeek/Tinfoil qualification.
