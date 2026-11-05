# Audit workbench frontend

Entry: `/audit-plan-playground/?workbench=1`. Without this query, the existing
builder remains the entry. The parent may route `/workbench` to the same entry.
Development Vite proxies `/api/workbench` to the UI backend on port 8090.

The workbench has no fabricated execution or catalog fallback. When bootstrap is
unavailable, unsigned local document/code authoring and export still work. A
successful HTTP response means the service replied; it is not an attestation or a
claim that inference is configured.

## HTTP assumptions for parent integration

All requests are same-origin, use cookie credentials, and accept JSON. The base is
`/api/workbench`. Errors use `{error:{code,message}}` and a non-2xx status. An HTML
login response is shown as unavailable. IDs in URL paths are encoded.

| Action               | Request                                                 | Response used                                                              |
| -------------------- | ------------------------------------------------------- | -------------------------------------------------------------------------- |
| Bootstrap            | `GET /bootstrap`                                        | `{user,capabilities,audits:[],datasets:[],plans:[]}`                       |
| Refresh catalog      | `GET /catalog`                                          | `{audits:[]}`                                                              |
| Refresh datasets     | `GET /datasets`                                         | `{datasets:[]}`; manifest metadata, not raw events                         |
| Import               | `POST /datasets` with `{dataset}`                       | Any JSON success object; then refetch manifests                            |
| Load saved agreement | `GET /plans/{id}`                                       | `{plan,digest,approvals?}`                                                 |
| Create/fork          | `POST /plans` with `{plan}`                             | `{plan,digest}`                                                            |
| Update               | `PUT /plans/{id}` with `{plan,expected_digest}`         | `{plan,digest}`                                                            |
| Check                | `POST /check` with `{plan}`                             | `{valid,diagnostics:[],requirements}`                                      |
| Rehearse/audit       | `POST /runs` with `{plan_id,dataset_id,mode}`           | `{run_id}`; mode `rehearse` or `audit`                                     |
| Run status           | `GET /runs/{id}`                                        | `{status,result?,...recipientSafeMetadata}`                                |
| Cancel               | `POST /runs/{id}/cancel`, no body                       | Any JSON success; poll to confirm cancellation                             |
| Approve/revoke       | `POST /plans/{id}/approve` or `/revoke` with `{digest}` | Any JSON success; reload exact digest and approvals                        |
| Challenge            | `POST /runs/{id}/challenge`                             | Any JSON success; refetch run                                              |
| Accountability       | `GET /history`                                          | `{history:[]}` or `{records:[]}`; other object shapes are displayed intact |

Import wrapping (`{dataset}`), approve/revoke body (`{digest}`), and challenge
fields are explicit frontend assumptions where the implementation contract does
not fix a body shape. Challenge bodies are:

```json
{
  "finding_id": "finding-1",
  "correction": "The external review artifact changes this conclusion.",
  "evidence_ids": ["authorized-review-reference"],
  "requested_remedy": "Review and amend",
  "recipients": ["authorized-auditor"]
}
```

No caller-supplied actor is sent. The server must authenticate the submitter,
verify correction artifacts, enforce reviewer/recipient policy, and retain the
original result and challenge disposition.

Bootstrap `plans` accepts raw plan objects or `{plan,digest,approvals?}` packages.
Catalog descriptors support `id,title,question,requirements,method,validity`, with
optional `plan` (full unsigned template) or `plan_id` (loadable saved template).
A matching bootstrap plan ID is also accepted. Starting a worked template forks
it to a new ID and drops its digest and approvals. A descriptor with no loadable
plan becomes a clearly labeled question-only draft with **no invented method**.
All API catalog rows are shown; the UI reports the actual count against the
27-audit target rather than claiming missing rows exist.

The run reader accepts findings/coverage/usage directly or under `result`. It
recognizes queued/running states and terminal `completed`, `complete`, `succeeded`,
`success`, `failed`, `blocked`, `cancelled`, `canceled`, `suppressed`, `released`,
`insufficient_evidence`. Unknown statuses remain visible and continue polling.
A missing status is a visible protocol error. Terminal status polling stops;
manual refresh and challenge submission can fetch a later amendment. Polling is
serial, abortable on unmount or run switch, with 1.5–5 second backoff. A network
error pauses polling with an explicit resume action and never changes execution
to a successful or clean state.

Optional recipient-safe fields rendered when supplied: `summary`, `scope`,
`unresolved_questions`, `trace`, `timeline`, `lineage`, `cohorts`, `challenges`,
`plan_digest`, `dataset_manifest`, `approvals`, `runtime`, `model`, `receipt`,
`verification`. These are not separate fetches for private raw data. A run or
result status/release state of `suppressed` withholds the findings panel, even if
the service accidentally also supplied a findings array. Trace authorization
and all other field projection remain mandatory backend responsibilities.

## Implemented behavior and boundaries

- Six editable agreement sections; criteria, exact scope, ordered operators,
  preceding-step links, complete JSON config, disclosure recipients and limits,
  participant/signing roster, expiry, budgets and extensible operation policies.
- Graph and JSON views edit the same document. Graph selection highlights its
  clause and selected JSON ID. Custom SQL/Python is explicitly custom code;
  descriptions do not pretend to interpret it. Empty operator configs remain
  incomplete until backend Check plan validates them.
- Saved-versus-draft semantic differences cover all top-level executable fields.
  Counterproposals support independent acceptance; question, criteria, scope and
  method changes are accepted as one dependent group. Any resulting combination
  needs backend validation. Applying suggestions never approves or executes.
- Exact-draft checks, optimistic save concurrency, saved-digest approval/revocation,
  one admitted dataset per run, and real rehearsal/audit calls. Any saved/check
  snapshot mismatch disables execution. The backend must bind the frozen plan,
  dataset/coverage commitments and all authoritative approvals at admission.
- Dataset manifests, source requirements and gaps, clearly labeled synthetic
  origin, JSON/file import, coverage/result/receipt inspection, evidence and
  counterevidence references, cancellation, authorized existing-run loading,
  challenge submission, and account-scoped accountability history.
- Backend admission denials with HTTP 403 or typed consent/approval/admission
  codes are explicitly displayed as blocked, without inventing a run ID.
- Local review notes have clause links, responsibilities and resolved states;
  they are labeled unshared/unpersisted and excluded from the signed plan.
- No browser storage of plan or evidence; drafts and notes remain in page memory.
  Unsaved executable edits warn on navigation away. Export is explicit.

No new dependencies or legacy schema changes are required.

## Gaps requiring additional service contracts

- **Labeled evaluation:** no evaluation HTTP endpoint is defined. The separate
  Evaluate with DeepSeek control is visibly unavailable and makes no rehearsal
  accuracy claim. Hidden labels must remain server-side.
- **Evidence-aware authoring assistant:** no workbench suggestion endpoint is
  defined. Counterproposal import works locally; it makes no model calls and
  sends no evidence to the legacy Anthropic authoring path.
- **Sealed confidential submission:** ordinary JSON import is explicitly not a
  verified submission client and is disabled in confidential mode. The parent
  must integrate a verified collector before confidential evidence upload.
- **Durable review discussions:** local notes work, but shared review threads
  need storage/API authorization. They are deliberately not claimed as saved.
- **Detailed visualization/data navigation:** timeline, cohort, lineage, runtime
  receipts and step trace objects are inspectable when released; dedicated
  interactive visualizations, evidence fetch links, per-step rerun and independent
  receipt verification need richer released-resource/action contracts. No
  arbitrary private-evidence URL or full-trace request is generated.
- **Runtime compatibility/privacy:** UI validation is advisory. The service owns
  operator semantics, verified inference, admission, consent invalidation,
  release suppression/accounting, schedule/retention enforcement and confidential
  trace policy. Additional approval/disclosure fields can be authored as JSON,
  but are not claimed as supported until the backend check accepts them.

## Verification

Run `corepack pnpm test`, `corepack pnpm typecheck`, `corepack pnpm build` in this
directory. Workbench tests use v1 fixtures, HTTP transport stubs, fake-timer
product polling, and static React rendering. They verify contract bodies,
scope/method/policy check invalidation, failure handling, suppression, explicit
outcome distinctions, counterevidence and recipient-policy disclosure. Transport
stubs are test-only; the product has no mocked response path. No live backend,
browser interaction, attestation, or real model execution is claimed by these
checks.

The full existing suite currently has one unrelated legacy-fixture failure:
`src/plan/plan.test.ts` expects deployed `plan_id` `destructive-commands`, while
the checked-in deployed fixture supplies `destructive_commands`. The legacy test
and fixture are unchanged by this workbench implementation.
