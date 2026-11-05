# Audit workbench frontend

Entry: `/audit-plan-playground/?workbench=1`; the existing builder remains the default
without the query. Vite proxies `/api/workbench` to port 8090. All product requests
use same-origin cookies, JSON bodies and authenticated server authorization. There
is no fabricated catalog, execution, signing, approval or model fallback.

## Review and agreements

Bootstrap discovers owner drafts and current authorized shared agreements. Refresh
agreements updates discovery; the API rejects ambiguous IDs. Owner manifest binding
uses `POST /plans/{id}/bind`, requires an operator-provisioned source grant, and
creates no approval. Shared readiness uses `GET /plans/{id}/readiness` and displays
only its boolean. Individual signer decisions, counts and times are never inferred
or rendered. The page cannot provision identities, roles, source grants, rosters or
release policies. Owner-only mutations can be denied when viewing a shared agreement;
the API currently exposes no ownership/action-capability metadata.

Saving retains optimistic `expected_digest` concurrency. Approval uses real
Ed25519 signatures, a nonextractable browser-held private CryptoKey in account-scoped
IndexedDB, fresh enrollment proof and nonce, and repeated local/server/session
freshness checks. Revocation applies to the saved version. These keys provide
browser-held consent, not confidential input or host resistance. Key loss requires
administrative recovery; no replacement enrollment or unsigned approval is invented.

Review notes now use `POST /plans/{id}/reviews` with exact saved `digest`, JSON-pointer
`clause`, `note`, and optional explicit `recipients`. Empty sharing means author-only.
The service stores author, full plan digest, clause commitment and recipient set.
Current and historical notes load with `GET /plans/{id}/reviews?digest=...`.
Historical revision digests can be entered explicitly; the API owns historical
access. Writes require the displayed saved version, never an unsaved draft.
Unsaved note text remains in page memory on failure, is explicitly labeled unsaved,
blocks agreement switching and warns before navigation away. Saving a plan does
not save a note. A revision change retains unsaved text and requires explicit
retargeting to the displayed saved version before storage. There is no invented responsibility, resolution or consent state.

## Method selection and execution

Catalog plans retain their mixed deterministic plus investigating-agent method by
default, using DeepSeek `deepseek-v4-1-flash` through Tinfoil. Catalog validity notes,
operator configuration diagnostics, evidence fields, join requirements, source gaps
and coverage limits are displayed from actual service fields. Operator validation
is not successful live model qualification.

“Create deterministic rehearsal variant” is an explicit user action. It copies the
currently displayed plan to a new unsigned editable identity, excludes agent steps,
retains model settings, and displays semantic differences against the source. It
explains the missing investigation of semantic ambiguity, alternatives and
counterevidence. If a retained step depends on an excluded agent, or no deterministic
method remains, creation is blocked for explicit authoring. It never strips agents
because inference failed and never runs or signs automatically. Every derivative
must be saved and checked anew. The API has no rehearsal-only plan type: a derivative
is an ordinary unsigned plan, with all ordinary approval/admission requirements if
someone later requests an audit.

Only the built-in `northstar` synthetic corpus can rehearse, matching the real API.
Rehearsal runs execute actual methods against fictional observations and are clearly
labeled as synthetic, not consented audits. Actual audit requests are labeled as
pending a signed recipient package until a receipt exists. A receipt is described
as signed, never independently verified until verification succeeds.

Run polling is serial, abortable and bounded; network errors pause monitoring with
an explicit resume action and leave execution state unknown. Queued/running and
unknown statuses continue polling. Completed, failed, blocked, interrupted,
cancelled, suppressed, released and insufficient-evidence variants stop polling.
Failures/denials establish no compliance finding. Cancellation, failure and
suppression do not refund admitted audit capacity. No automatic execution retry is
performed. Admission errors are shown without inventing a run ID.

## Recipient results, challenges and verification

`GET /runs/{id}` provides only the caller's authorized projection and receipt for an
audit, and owner synthetic results for rehearsal. “Retrieve my authorized package”
uses `POST /runs/{id}/release` with no destination or authorization JSON. Included
subjects/signers are not assumed to be result recipients. The frontend never fetches
private traces, raw evidence, or arbitrary ID-based resources.

Results show finding prose, supporting and counterevidence reference cards,
limitations, coverage, released outcome counts, and supplied timeline/cohort/lineage
records as readable cards. Optional fields are omitted when absent. No chronology,
causal links, totals, populations or coverage values are synthesized. The current
governed audit projection does not supply timeline, lineage, trace or evidence IDs;
those views therefore remain absent or explain that references were not released.
An agreement's evidence/excerpt flags are upper bounds; the current API delivers
neither to audit recipients. Full suppression also hides findings, counts, cohorts,
timeline and trace even if accidentally supplied. Actual suppressed findings
objects (`{state:'suppressed',value:null}`) are handled without array crashes.
Suppression and unknown coverage never become zero or a clean audit.

Challenge submission uses the actual finding/correction/evidence/remedy/recipient
shape. At least one explicit recipient and an available released finding are
required. Only owners can attach scoped evidence IDs; recipient evidence delivery
is unsupported. `GET /runs/{id}/challenges` loads only caller-authorized immutable
history with correction text/commitments and parent/amendment links. A reviewer
request form sends `parent_id`, disposition and optional existing corrected release
ID, with no fabricated role or approval metadata. The service must authorize the
independently enrolled role, inclusion, conflicts, narrowed recipients and corrected
release linkage. No role/capability endpoint exists to pre-certify eligibility;
the UI explicitly calls this a request, not an authorized status. Synthetic
challenges are owner-only, durable, unverified and have no governance disposition.

Receipt verification runs locally in WebCrypto with an independently supplied,
base64 raw Ed25519 public key. It never fetches `/receipts/public-key` or trusts an
embedded/self-published key. It verifies RFC8785 canonical signed payload bytes,
the domain-separated SHA256 projection commitment, receipt version, and optional
user-supplied expected bindings. An exported `{projection,receipt}` package can be
verified without any run lookup or account data. The receipt run ID is the internal
governance ID, not the UI route ID. Without independently expected bindings,
verification only authenticates the supplied package. It proves issuer/bytes/bindings,
not factual correctness, completeness, voluntary consent or confidential execution.

## Current remaining gaps

- Live DeepSeek/Tinfoil transport qualification, measured confidential execution,
  sealed input, encrypted deployment and independent key distribution remain parent
  integration work. This page supplies no runtime-fact assertions or attestation.
- Evidence-aware authoring and labeled evaluation have no HTTP service endpoints.
  The existing evaluation control remains disabled; counterproposal import is local,
  with no model call or mock result. Rehearsal does not measure model effectiveness.
- The API exposes no reviewer-role or per-action authorization capabilities. Owner
  binding and disposition requests rely on service authorization and report denials.
- Native audit evidence references/excerpts, raw step drill-down, per-step rerun,
  recipient correction evidence delivery and richer timeline/cohort/lineage packages
  have no release/resource contract. No unauthorized fetch path is added.
- Challenge delivery notifications, deadlines, attempt limits, independent anti-coercion
  process and numerical correction budget exemptions are absent. Original packages
  remain immutable; overlapping corrected analyses remain governed by normal limits.
- Notes are append-only. No resolve/delete/edit endpoint or revision-list endpoint is
  invented. Historical notes require a known authorized revision digest.
- Browser IndexedDB persistence across real profile restarts, live enrollment and
  end-to-end backend/browser operation still require integration qualification.

## Verification

Run `corepack pnpm test`, `corepack pnpm typecheck`, `corepack pnpm build` here.
The original 99 tests remain. Additional mounted React tests exercise actual note
forms and API bodies, persisted-note reload, failure honesty, authorized shared
readiness, explicit derivative creation, challenge/disposition lineage and self-only
release requests. Receipt tests use real WebCrypto Ed25519/SHA256 and cover altered
projections, foreign keys, wrong governance run bindings and UI verification of an
exported package without network calls. HTTP responses are test-only transport
fixtures; there is no product mock path. React test renderer is a development-only
dependency. These checks do not claim live browser, backend, model or enclave execution.
