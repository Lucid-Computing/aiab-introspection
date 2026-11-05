# Governance integration

`aiab.workbench.governance.GovernanceStore(path)` is a synchronous, durable SQLite
consent/disclosure gate. Use a persistent local database shared by all service
workers, not one database per account. It uses `BEGIN IMMEDIATE`, WAL, FULL
synchronization and a 30-second lock timeout. New database files use mode 0600.
A POSIX `flock` sidecar serializes first-open WAL initialization; keep that sidecar
while workers are active (local POSIX filesystem required). Run blocking operations outside the
async request loop. It imports evidence-owned `contracts.validate_plan` and
`contracts.plan_digest` lazily. Other commitments are RFC8785/SHA256 with the UTF-8
domain, a NUL byte, then canonical JSON. These are new workbench semantics only.

## Required trusted integration

An `actor` string is **not authentication**. Parent HTTP routes must derive it from
authenticated server sessions and enforce CSRF, authorization and request limits.
Never copy identity, roles, public-key enrollment, source grants, runtime facts,
aggregate validation, privacy domains or authority operations from request JSON.
Approval signatures are independently verified Ed25519 proofs against enrolled
identity keys; enrollment must itself verify the real principal/key association.
The store cannot establish voluntary consent or rule out coercion.

Trusted administration calls these methods before exposing an audit:

- `enroll_identity(actor, public_key, *, roles=())`: immutable identity/key binding;
  key is base64 raw 32-byte Ed25519. Roles include `employer`, `roster_authority`,
  `release_authority`, `source_authority`, `challenge_reviewer`. This method is an
  administrator-only seam, not a public self-enrollment endpoint.
- `freeze_roster(roster_id, subjects, *, actor, source_commitment, scope_id)`:
  independent roster authority freezes an exact full population. Neither ID nor
  scope can be replaced or shrunk. `scope_id` equals the confidential audit's stable
  privacy domain. Organizational membership migration is deliberately unsupported;
  do not invent a fresh domain to evade an existing refusal or release budget.
- `configure_release_policy(domain, *, actor, families, max_releases=1,
  min_group_size=2)`: immutable independent release policy. Each family is
  `{subjects: [...], fields: [...], query_digest: governance.query_digest(plan)}`.
  The digest fixes mode, question, criteria, scope, steps and agent configuration.
  Scope must be fully explicit, including nonempty subjects and dataset IDs.

Independently authorize the **complete dataset manifest**, including content and
resource commitments, source permissions, observed universe, transformations,
identity maps and snapshot/version references. The gate binds its canonical bytes;
it does not check that those bytes describe the real dataset. Pass a verified
`authorization = {dataset_ids: [...], subjects: [...], privacy_domain: str,
grant_id: str, expires_at: ISO8601-with-timezone | Unix-seconds | null}`. Subjects
and dataset IDs must exactly match the plan. Assign privacy domains independently
of requester, plan ID, dataset snapshot and recipient; the domain must cover all
linkable evidence versions/accounts. `expires_at: null` explicitly means no expiry.
The runtime must intersect source/field/time permissions at **every** evidence/tool
access; the gate does not filter input records. Call `revalidate(run_id)` between
tools and before inference, and cancel execution on denial. Revocation cannot recall
bytes already sent to an authorized runtime or recipient.

## Service API

All successful mutations return JSON dictionaries; denial raises `ValueError`.
Do not expose raw denial reasons or arbitrary exception details to employer clients;
use generic blocked/not-found responses. Use `readiness` for consent UX.

| Method | Arguments / result |
| --- | --- |
| `register_plan` | `(plan, owner, *, dataset_manifest=None, authorization=None, roster_id=None)` → `{plan, digest, binding_digest}`. Both optional input arguments are required before approval/admission. Re-register an unbound draft to freeze them. A bound digest is immutable. Registering a new version supersedes the old version; historical approved versions cannot be reactivated. Parent implements compare-and-swap for HTTP edits. |
| `approval_payload` | `(plan_digest, actor, *, expires_at=None, nonce)` → exact object to sign with `canonical(payload)`. Binds signer, plan, manifest, source authorization, recipients, frozen roster and expiry. |
| `approve` | `(plan_digest, actor, *, signature, nonce, expires_at=None)` → `{approved: true}`. Base64 Ed25519 signature; actor's nonce is single-use across plans. No unsigned/mock approval path. |
| `revoke` | `(plan_digest, actor)` → `{revoked: true}`. Revokes that signer's grant and permanently blocks pending runs, including after reapproval. |
| `invalidate_authorization` | `(plan_digest, *, actor)` → `None`; trusted source authority permanently invalidates the source grant across existing and future plan versions in its privacy domain. Integration must propagate external source-permission revocations before subsequent access/release. |
| `readiness` | `(plan_digest, actor)` → `{ready: bool}` only; no signer states/counts/times. |
| `admit` | `(plan, actor, dataset_manifest, *, request_id, authorization, runtime_facts=None)` → `{run_id, binding_digest}`. Owner-only; requires owner, all subjects and every explicit required signer. Replayed request IDs are rejected. Reserves capacity permanently, freezing the original approvals. |
| `revalidate` | `(run_id)` → `{authorized: true}`. Trusted runtime seam, never a public run-discovery endpoint. |
| `cancel` | `(run_id, actor)` → `{cancelled: true}`; owner-only, no reservation refund. |
| `release` | `(run_id, result, recipient, *, actor=None, authorization, validation=None)` → `{projection, receipt}`; authenticated actor must equal recipient. Rechecks active version, original/current consent expiry, source-grant expiry/revocation, cancellation and runtime freshness. First release fixes result digest for all recipients. Each recipient gets at most one immutable package. |
| `plans` / `history` | `plans(actor)` → `[{plan,digest}]`; `history(actor, *, plan_digest=None)` → minimal authorized event metadata. These are the only discovery feeds; do not expose raw tables/ledger. Approval, refusal, revocation and private challenge events are omitted completely from the feed. |
| `run_status` | `(run_id, actor)` → `{run_id,status,releases}`; owner or already-released recipient, only that actor's saved projections. Never return the private runner result. |
| `challenge` | `(run_id, actor, *, claim_id, correction_commitment, requested_remedy, recipients, parent_id=None, disposition=None, corrected_release_id=None)` → immutable challenge record. Recipients must be authorized principals; child recipients can only narrow. |
| `challenges` | `(run_id, actor)` → only that actor's permitted challenge records, in ledger order. |
| `receipt_public_key` | `()` → base64 raw Ed25519 verification key; pin through an independent trusted channel. |
| `verify_receipt` | `(package, public_key, *, expected=None)` → `True` or `ValueError`; verifies signature, projection digest, optional exact expected receipt fields (e.g. run/recipient/plan/binding). |
| `checkpoint` | `()` → `{seq,hash}`; export to an independent durable witness. |
| `verify_ledger` | `(*, external_checkpoint=None)` → `{verified,seq,hash,externally_anchored}`; verifies every link and detects missing/changed externally pinned history. |

Personal mode requires authenticated owner = sole subject = sole recipient and
rejects employer identities. It remains invisible to employer discovery/history,
readiness and run access. Joint mode also requires every included subject's consent;
confidential mode requires the independently frozen full organization roster, with
an authority other than the owner. Plan recipients and required roster are public
agreement data; actual approval/refusal/revocation states remain private.

## Confidential runtime and output boundary

Confidential admission is unsupported unless trusted attestation/ingestion code
supplies `runtime_facts` with all of `attestation_verified`, `sealed_input_verified`,
`client_verified`, `plaintext_ui_absent`, `protected_execution_verified` exactly
`true`; also `runtime_digest`, `plan_digest`, a future `expires_at`, and
`input_commitment = commitment('aiab-governance/dataset-manifest/v1', manifest)`.
These assertions must result from actual verification, including runtime/model/tool
pins, submission-client update trust, sealed input and protected trace storage.
An ordinary UI holding plaintext must not set them. No attestation verifier or
confidential ingestion is implemented by this module. Runtime facts expiring during
a run fail release; this implementation does not renew them silently.

Confidential output supports only `counts`, `coverage`, `status`, with evidence and
excerpts disabled. Its trusted runtime validator must independently establish one
classification per exact authorized subject and supply
`validation = {aggregate_subjects_verified: true, binding_digest,
result_digest: commitment('aiab-governance/result/v1', result)}`. The store also checks
finding cardinality/outcomes; cardinality alone does not prove correct attribution.
This validation object is an in-process trusted seam, not a cryptographic runtime
certificate. Wrong binding/result commitments fail release.

Other modes can also select `findings`: freshly constructed opaque local IDs,
outcome, criterion ID, title, claim, method and string limitations only. Free text
must be independently reviewed for permitted content; schemas cannot prevent covert
encoding in prose. Evidence IDs, excerpts, details, arbitrary coverage metadata,
usage, raw input, private traces and unrecognized nested fields are never copied.
The gate narrows `allow_evidence`/`allow_excerpts`: it does not implement authorized
reference/excerpt delivery yet. Coverage exposes `available`/`unknown` only.

Every nonzero outcome cell below the larger policy/plan group threshold suppresses
**all** counts/complements, and insufficient population suppresses the package's
non-status fields. Suppression is `{state:'suppressed',value:null}`, never zero.
Fixed permitted query commitments, global bounded reservations, and prohibition of
**all intersecting subject populations** in a privacy domain prevent repeat-query
and differencing bypasses across plan versions/accounts/recipients. Disjoint
families can consume the finite global budget. One reservation permits delivery of
the same committed result to all preauthorized recipients. Cancellation, failure,
crash and suppression do not refund capacity. This intentionally conservative
restricted-release policy is **not differential privacy**. Numerical corrected
reports requiring an overlapping new analysis remain blocked; there is no budget
reset or correction exemption.

Challenges preserve originals and link child records by immutable IDs. Dispositions
are `upheld`, `amended`, `withdrawn`, `unresolved`, require a parent and independent
`challenge_reviewer` who is neither requester, original challenger nor parent author. Reviewers must be
preauthorized agreement recipients/signers and explicitly included in the challenge.
Correction evidence is an authorized committed reference, never raw challenge text
in the public feed. A linked corrected release must already have passed ordinary
fresh authorization/accounting and be a different run with the same owner/domain;
unrelated/self links fail. Challenge evidence storage, delivery notifications,
deadlines, attempt/rate limits and a real independent anti-coercion process belong
to parent integration. No method reinterprets or overwrites a prior finding.

## Persistence and verification limits

Ledger UPDATE/DELETE triggers and hash links detect ordinary tampering. An external
checkpoint detects rollback/rewrite of the pinned prefix; exporting a checkpoint
only to this same database provides no independent assurance. `verify_ledger` does
not prove mutable database state corresponds to every event, source completeness,
model correctness, or freedom from administrator compromise. Admins/filesystem/key
access remain trusted. The receipt signing key is persisted in this local database;
protect it with filesystem permissions/encrypted storage or replace that seam with
a reviewed external signer before production. Backups must include SQLite WAL or
use SQLite backup. Retention/key lifecycle, encrypted artifacts and multi-host
consensus are deployment responsibilities. Successful decisions are ledgered in
the same transaction; rejected HTTP attempts must be logged by the authenticated
parent service without exposing refusal identities. There is no public full-ledger
endpoint. Receipt verification authenticates issuer/bytes/bindings, not factual
correctness or confidential execution by itself.

Tests use real Ed25519 and real SQLite restart/parallel-request behavior. While the
evidence-owned module is absent they install a narrow test-only contract adapter
(digest/validation), without creating or changing `contracts.py`; once present,
tests use the actual module. Runtime facts and aggregate validator assertions in
unit tests are explicit trusted fixtures, not claims of live enclave execution.

## Authenticated workbench HTTP workflow

The UI adapter uses authenticated session usernames and exact same-origin mutations.
`POST /plans` and `PUT /plans/{id}` retain their existing shapes; PUT uses
`expected_digest`. After an agreement has been bound, an edit registers its new
unsigned draft while holding the local editor CAS transaction. Superseded signatures
cannot approve/admit/release the new version, and historical approved versions cannot
be restored. A failure between the two SQLite commits can advance governance ahead
of the editor: this fails closed and requires operator reconciliation, never fallback
to the prior consent. Local current-version checks also gate execution and release.

Minimal frontend additions (all paths below have `/api/workbench` prefix):

| Endpoint | Request / response |
| --- | --- |
| `POST /plans/{id}/bind` | Owner freezes an operator-authorized manifest before inviting signers; `{bound,digest}`. No signing consent is created. Existing owner `approval-payload` also binds. |
| `GET /plans`, `GET /bootstrap` | Owner drafts plus current authorized agreements. No approvals, refusals, signer state/count/timing, identities, or private datasets from other accounts. Ambiguous plan IDs are rejected. |
| `GET /plans/{id}/readiness` | `{ready:bool}` only. |
| `POST /plans/{id}/reviews` | `{digest,clause,note,recipients?}`; `clause` is an exact JSON pointer. Stores actor, full plan digest, clause commitment, text and explicit authorized recipients. Default visibility is author-only. Review is never signing consent. |
| `GET /plans/{id}/reviews?digest=...` | Exact-version notes visible to caller; historical owner revisions and historically authorized agreements are supported. |
| `POST /runs` | Existing `{plan_id,dataset_id,mode}` plus optional `request_id` for audit admission replay protection. Only the built-in corpus can rehearse. |
| `GET /runs/{id}` | Rehearsals are owner-only and labelled synthetic. Audit owners and explicit recipients receive only their governed projection and signed receipt; included subjects/signers are not thereby result recipients. Queued/running status contains no result or private error. |
| `POST /runs/{id}/release` | Authenticated recipient releases to self only; no caller-selected destination or authorization JSON. Returns `{projection,receipt}`; repeated retrieval returns the immutable saved package after fresh authorization checks. |
| `GET /receipts/public-key` | Public `{algorithm:"Ed25519",public_key}`. Pin independently. |
| `POST /receipts/verify` | Public read-only `{package:{projection,receipt},public_key,expected?}` verifies with caller-pinned key and optional exact receipt bindings, without any private job lookup. It authenticates issuer/bytes/bindings only. |
| `POST /runs/{id}/challenge` | Existing frontend shape `{finding_id,correction,evidence_ids,requested_remedy,recipients}`; optional `parent_id,disposition,corrected_release_id` for review lineage. Finding must exist in caller's released findings (or owner synthetic findings). |
| `GET /runs/{id}/challenges` | Authorized challenge records in lineage order plus durable correction text. Historical challenge visibility follows the original agreement. Evidence IDs remain owner-only. |

Audit UI run IDs route to internal governance run IDs; the signed receipt's `run_id`
is the governance ID. Use the receipt's exact identifier for independent expected
bindings. The API never returns a private audit result, trace, usage, input, arbitrary
finding details, or evidence IDs to a recipient. The projection schema does allow
reviewed finding prose; it cannot detect secrets/covert encoding in free text.
Owner review of raw audit traces is not exposed through these routes either.

Challenges do not mutate original results or receipts. A finding already released
to a principal remains challengeable after withdrawal/cancellation, using only that
saved projection; attaching new evidence still requires fresh source authority.
Ordinary challenges store
private correction material durably and ledger its commitment. Review dispositions
require the independently enrolled reviewer role, inclusion in the parent challenge,
no owner/challenger conflict, and narrowed recipients. Corrections linked to another
release must satisfy the ordinary governance rules; overlapping numerical analyses
have no budget exemption. Recipient correction evidence references are currently
unsupported: only an owner can attach IDs resolving through their scoped evidence
store, and those IDs are never delivered to other principals. Synthetic challenges
are durable, owner-only, explicitly `synthetic_unverified`, and have no governance
receipt, disposition, or claimed factual verification. Delivery/notifications,
deadlines and attempt limits remain unimplemented.

## Offline operator provisioning

Run from the UI Python environment with `PYTHONPATH=ui:static` (or equivalent
installed packages):

```
python -m server.workbench_admin --directory /persistent/workbench identity identity.json
python -m server.workbench_admin --directory /persistent/workbench frozenroster roster.json
python -m server.workbench_admin --directory /persistent/workbench releasepolicy policy.json
python -m server.workbench_admin --directory /persistent/workbench sourcegrant grant.json
python -m server.workbench_admin --directory /persistent/workbench revoke-source revocation.json
```

Every input MUST contain
`"trust":"imported_operator_authorization_not_independently_verified"`.
This is an explicit trust acknowledgement, not evidence that the CLI verified the
source, principal, roster, or voluntary consent. Administrators must establish these
facts independently. Never import a private signing key. Browser enrollment proves
possession against the authenticated session; it does not establish employer roles.
Identity keys/roles are immutable, so provision privileged identities before browser
enrollment (later roles require a new identity). No command generates identity keys,
signs approval, fills absent consent, or manufactures runtime facts.

Input fields in addition to `trust`:

- `identity`: `actor,public_key,roles?`; roles are `employer,roster_authority,
  release_authority,source_authority,challenge_reviewer`.
- `frozenroster`: `authority,roster_id,subjects,scope_id,source_commitment`.
- `releasepolicy`: `authority,domain,max_releases,min_group_size,families`;
  each family maps a chosen ID to `{plan:<exact reviewed plan>,fields:[...]}`.
  The CLI computes the query commitment. Independently configure every permitted
  query family/version before binding; requester edits do not grant new families.
  The release authority must differ from the owner.
- `sourcegrant`: `authority,owner,plan,authorization,dataset_manifest,
  source_commitment,roster_id?`. `authorization` is the exact governance object
  documented above, including explicit `expires_at` (null means no expiry).
  `dataset_manifest` must be `{dataset_digest:contracts.dataset_digest(dataset),
  manifest:dataset.manifest}` for the complete independently authorized corpus.
  The CLI checks source-authority role and exact dataset/subject sets, stores the
  provenance/trust label, and atomically writes mode-0600 `source-grants.json`.
  Admission compares the entire manifest to the owner's immutable corpus.
- `revoke-source`: `authority,plan_digest`; permanently invalidates the source grant
  across versions/runs in the privacy domain. Removing the local grant also denies
  subsequent event checkpoints/releases; use permanent revocation for withdrawal.

Use exclusive operator access when provisioning the source-grant file; concurrent
operator file writers are not serialized. Legacy manually installed grants remain
trusted operator assertions; the API does not reinterpret them as independently
verified provenance. HTTP cannot provision roles, rosters, policies or source grants.

## Runtime integration status and exact remaining boundary

The adapter invokes runtime `budgets`, `graph_order`, and `validate_operators(live=True)`
for `/check`, each catalog's `runtime_validation`, and admission. This is operator
configuration validation, not a successful live DeepSeek/Tinfoil qualification.
Model verification must occur inside execution; MR_SEAM failure has no bypass.
Joint execution receives a trusted capability dict derived solely from the operator
source grant: `{id,authorized_dataset_ids,authorized_subjects}`. Synthetic rehearsal
capabilities derive solely from the server-generated built-in corpus. Personal mode
continues to intersect the actual authenticated subject in the runtime.

The adapter re-reads source grants, full manifest and current plan binding, then calls
`GovernanceStore.revalidate` before/after execution and on **every available private
async `on_event` callback**. Callback payloads never go to HTTP. An authorization denial
discards completion; explicit cancellation also cancels the task and wins over a late
finish. Source authorization is not accepted from request JSON.

The merged runtime now supplies `authorize=callback`, invoked immediately before
evidence access, tokenization and inference. The API wires a zero-argument callback
that checks durable cancellation state, current source grants and governance consent
and returns exactly `True`. It retains independent admission/release checks and
private event checkpoints. A request already sent cannot be recalled. Confidential
UI admission remains blocked until actual measured deployment and independently
verified sealed submission are qualified; no runtime facts are manufactured.
