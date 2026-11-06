# Builder tester findings

**Resolution:** WB-B01/B02 are fixed locally; see [implementation resolution](#implementation-resolution--2026-11-06). Original evidence follows unchanged.

Tested 2026-11-06 UTC by the independent Sol tester in worktree
`t3-0b9da043`, baseline `f3aec71` (application code `e404703`). Hosted target:
https://aiab-dev-astra-one.onrender.com/workbench, deployed commit supplied by
operator as `e404703`; deployment identity was not independently attested.
Read `docs/WORKBENCH_DEMO.md` and `docs/WORKBENCH_IMPLEMENTATION_CONTRACT.md`.
No application fixes, pushes, deployments, provider changes or live inference.

## Prioritized findings

### WB-B01 — P1/high: execution does not bind the displayed/check-passed revision

**Expected:** admission atomically compares the displayed saved digest and refuses
an intervening save. Evaluation should evaluate the revision that passed its
preflight, or return a conflict. An ordinary rehearsal should likewise execute
the agreement the operator checked.

**Actual:** run/evaluation POST bodies identify only a mutable plan ID. Evaluation
performs a GET comparison first, but another tab can save after that GET and before
the POST. The server then snapshots the newer revision without a version conflict.
The report truthfully displays the newer digest afterward; that does not protect
the operator's pre-execution choice. This also leaves a potentially costly live
execution exposed, although no live inference was attempted here.

**Exact hosted reproduction:** authenticate through GET `/login`, then POST the
`username`, `password`, `next=/workbench` form read privately from the dedicated
credential file. Retain the cookie in memory and send same-origin
`Origin: https://aiab-dev-astra-one.onrender.com` for mutations. Use JSON requests:

1. GET `/api/workbench/bootstrap`. Clone its first inline catalog `plan`. Assign
   a fresh `sol-builder-tester-<uuid>` ID and title `Sol tester synthetic revision A`.
   Remove agent steps and their transitive dependants, leaving deterministic
   steps. Never modify an existing catalog/demo plan.
2. POST `/api/workbench/plans` with `{plan: A}`; retain returned digest `dA`.
   POST `/api/workbench/check` with `{plan: A}` → `valid: true`.
3. GET `/api/workbench/plans/{id}` → A, `dA`. This is the evaluation preflight.
4. Simulate a second tab: PUT the same test-owned plan with only its title changed
   to `Sol tester synthetic revision B`, using `{plan: B, expected_digest: dA}`.
   Retain `dB`.
5. POST `/api/workbench/evaluations` with
   `{plan_id: id, include_agent: false, case_limit: 1, seed: 0}`.
6. GET `/api/workbench/evaluations/{evaluation_id}` → `plan_digest == dB`, not
   `dA`. POST its `/cancel` with `{}` → `cancelled`.

**Preserved hosted evidence (test-owned artifacts intentionally retained):**

- Plan: `sol-builder-tester-286428d55a8e`.
- A digest: `0de8b2e9fcec55eecbc5c1300a0c9cc9a2587a19b37faaf3f9d10a78d68668eb`.
- B digest: `0e0a52e7946eba297cb74dd5bfdba6eddf03d5753c56300127d84dc71e11e382`.
- Evaluation: `cbdc0df0ec504c669e088f18027bb908`.
- First GET status: `running`, B digest match `true`, reviewed A match `false`.
- Evaluation cancellation response and final GET: `cancelled`; deterministic
  mode, cap 1, seed 0.
- Rehearsal run: `1ed8a6c7066641e79d72d01ac83f20a9`. POST `/api/workbench/runs`
  with `{plan_id: id, dataset_id: "northstar", mode: "rehearse"}` while the
  operator still holds A returned a run whose first GET was `running` and bound
  to B. Its `/cancel` response and final GET were both `cancelled`. The saved
  test plan was asserted to contain no agent steps before starting it.
- Control: a subsequent stale PUT using `dA` returned `409`, error code `conflict`.
  Save optimistic concurrency works; the defect concerns execution binding.

**Paths:** `ui/playground/src/workbench/api.ts:61` and `:85` omit a digest;
`Evaluations.tsx:98` compares before a separate POST at `:100`;
`WorkbenchApp.tsx:263` checks only local saved/check state before run creation;
`ui/server/workbench_assist.py:664` rejects extra evaluation inputs and `:678`
loads the current plan; `ui/server/workbench_api.py:573` likewise loads the current
plan for runs. New test `builder-tester.test.tsx:87` records the missing wire
binding (a prospective final digest argument, ignored by the baseline API).
Both evaluation and deterministic rehearsal are confirmed over hosted HTTP; the intervening-save timing is reproduced by sequential requests, without
a browser race or live-agent run. No authorization or signature
bypass is claimed: governance still verifies the version it actually admits.

### WB-B02 — P2/medium: navigation silently loses code and settings drafts

**Expected:** retain an unapplied buffer across view changes, or require an
explicit discard before destroying it. Include these buffers in dirty/navigation
protection. This is separate from applying them to the executable plan.

**Exact component reproduction:**

1. Mount `WorkbenchApp` with a successful synthetic bootstrap; choose Code.
2. Edit Plan JSON (valid changed title or incomplete JSON), without Apply.
3. Choose Agreement, then Code. Actual: original plan JSON replaces the edit.
4. Repeat with only a code-buffer edit, then choose `+ New agreement`.
   Actual: the agreement switches immediately; the code draft disappears.
5. In Agreement, edit `Complete scope contract` without Apply settings. Choose
   Code and return to Agreement. Actual: the scope buffer resets as well.

**Evidence:** three desired-behavior assertions in the new test file (`:44`,
`:51`, `:57`) fail on baseline: returned code differs from the typed JSON;
Plan JSON textarea count becomes zero after New; scope text returns to the old
scope. The tests are explicitly marked `it.fails`, not hidden as successful
behavior. Other ordinary tests mount the same app successfully.

**Paths:** `Views.tsx:136` owns the code locally; `Editor.tsx:74` owns JSON settings
locally. `WorkbenchApp.tsx:572` and `:588` unmount those editors on tab changes.
The dirty expression at `:72` and switch guard at `:172` see only applied plan
changes and review notes. The unload guard similarly excludes these buffers.
The interface explicitly says unapplied buffers are excluded from saving and
execution; that exclusion is a documented gate and passes a control test. Silent
loss during ordinary navigation is the usability defect. No browser-human
usability study was conducted.

## Checks, controls and limitations

Hosted bootstrap succeeded with **27 audits, all 27 inline plans, one dataset**.
Only the unique test-owned plan above, its evaluation and its rehearsal were created/updated. Both jobs
were confirmed cancelled.
No existing saved agreements were opened or changed. Credentials/cookies/provider
payloads were never printed or committed. WorkOS callback/OIDC was not tested;
this used the dedicated password login as authorized.

The new component tests additionally exercise rejected-save draft preservation,
a late proposal response after a concurrent edit, graph-inspector edits retaining
config/extensions, local revision change during evaluation preflight, rejected
cancellation retaining active controls, and failed counterevidence-source lookup
retaining outcome/coverage/limitations and retry. Graph canvas is stubbed because
React Flow requires a DOM; the real inspector is tested. Network calls in these
component tests are explicitly mocked. Source/HTTP/component behavior assesses
usability; keyboard, layout, focus, visual graph rendering and browser-human
usability are unverified.

Existing tests cover atomic dependent proposal acceptance, explicit acceptance
before mutation, exact-plan round trips/extensions, unchecked/dirty execution
gates, terminal polling/abort/failure, evaluation bounds and retained IDs,
partial results, family denominators, abstention/citation errors, suppressed
counts, and readable evidence/counterevidence/outcome distinctions. These are
bounded tests, not production accuracy or confidentiality qualification.

Documented gates are not counted as defects: source grants/rosters/independent
consent and release policies require operator provisioning; confidential Render
admission remains blocked; unavailable verified inference must fail without a
fallback; imported datasets cannot qualify as built-in Northstar rehearsals;
insufficient evidence, failure, suppression and no finding remain distinct.

## Reproduce local verification

From the repository root:

```sh
cd ui/playground
pnpm install --frozen-lockfile
pnpm test
pnpm typecheck
node --input-type=module - <<'JS'
import { startVitest } from 'vitest/node';
const ctx = await startVitest('test', [], {
  include: ['src/**/*.test.ts', 'src/workbench/*tester*.test.tsx'], watch: false,
});
if (!ctx || !ctx.state.getFiles().length) process.exitCode = 1;
else {
  process.exitCode = ctx.state.getFiles().some(f => f.result?.state === 'fail') ? 1 : 0;
  await ctx.close();
}
JS
```

The custom invocation is required because `vite.config.ts:22` includes only
`*.test.ts`; the assigned test filename is `*.test.tsx`. Configuration was not
changed outside tester ownership. Baseline `pnpm test`: **128 passed**. Combined
invocation: **135 passed, 4 expected failures, 139 total, 6 files**. New file alone
contributes seven passing controls and four known-defect assertions. Typecheck
passes. `it.fails` markers must be removed when those behaviors are fixed; an
unexpected pass is reported as a failure.

Backend integration verification:

```sh
PYTHONPATH=ui uv run --project ui pytest ui/tests/test_workbench_assist.py ui/tests/test_workbench_api.py ui/tests/test_workbench_store.py -q
```

Backend result: **74 passed in 362.42 seconds**.
No standalone static-suite run or real-model qualification was performed by this
tester. Backend tests use their declared fixtures/test models and real local
sandbox paths; these are not hosted inference checks.

## Implementation resolution — 2026-11-06

WB-B01 and WB-B02 are resolved in this implementation worktree. The original
baseline findings, hosted artifact IDs/digests and limitations above are retained
as historical evidence; this resolution is local verification, not a claim that
the hosted deployment has changed.

**WB-B01:** Both execution POSTs now require `expected_digest`. The run client
sends the saved digest whose plan matches the current successful check for both
rehearsal and audit; the evaluation client sends that digest in its POST as well
as retaining its local/preflight safeguards. Missing/empty/non-string values
return `400 expected_digest_required` with an explicit message. A stale digest
returns `409 conflict` before runtime checks, governance admission/reservations,
queue creation or inference. `WorkbenchStore.execution_snapshot` obtains the
body and digest from one SQLite SELECT, compares the supplied digest and returns
a detached snapshot. Both queues persist that snapshot rather than looking up
the mutable plan ID again. A save after that read can either supersede governance
authority (which execution still rechecks) or coexist with a queued rehearsal;
it cannot silently replace the queued revision. Actor, login, authentication and
principal handling were not changed.

Sequential backend reproductions save/check/read A, save B with A's digest, then
POST the old digest. Both run modes and evaluation explicitly reject the request;
spies forbid reaching runtime/admission/queue submission and database assertions
confirm no job rows. Separate tests save B after snapshot selection but before
queue persistence/submission and verify the queued run/evaluation retains A.
All existing affected callers supply the required field. Frontend exact-wire
assertions cover rehearsal, audit and evaluation.

**WB-B02:** Unapplied code and JSON settings now live in a shared draft registry,
keyed by editor/step, and survive view unmounts. They count as dirty for agreement
navigation, unload protection, save, consent and execution gates. Opening another
agreement is blocked with an explanation until the operator applies/resets the
buffers or explicitly uses Discard changes. Reset code buffer and Reset settings
buffer discard only the relevant typed buffer; Discard changes clears all typed
and applied changes. Invalid JSON is retained after failed Apply and is never
automatically applied. If the underlying document changes while a buffer exists,
Apply is disabled until the operator resets it or explicitly chooses to keep the
buffer against the current document. Applying still validates the JSON/plan.
Buffers are session memory, not persistent recovery after closing the page.

All four builder tester `it.fails` assertions are now ordinary passing
regressions. Normal `pnpm test` includes `.test.tsx`; the custom baseline invocation
above is no longer needed. Additional component tests cover failed Apply,
individual reset, explicit discard/new agreement, unload protection, code and
settings divergence, and a late proposal after switching to a new agreement.
The existing concurrent-edit/late-proposal regression remains passing. The former
save-exclusion control now asserts that unapplied buffers block saving entirely.
Authorization tester markers were not modified.

No live inference, browser preview, provider changes, PR, push or deployment was
performed. Backend tests use their existing local sandbox and labeled fixtures;
snapshot-specific tests stub execution/admission to assert ordering and data.

Final local verification:

```sh
cd ui/playground
pnpm test
pnpm typecheck
pnpm build
# From repository root:
PYTHONPATH=ui uv run --project ui pytest ui/tests/test_workbench_assist.py ui/tests/test_workbench_api.py ui/tests/test_workbench_store.py ui/tests/test_tester_workbench_authorization.py -q
uv run --project ui ruff check ui/server/workbench_api.py ui/server/workbench_assist.py ui/server/workbench_store.py ui/tests/test_workbench_api.py ui/tests/test_workbench_assist.py
```

- Frontend: **145 passed, six files**, including **17 builder tester regressions**;
  no expected failures in that file. Typecheck and production build pass.
- Backend digest/snapshot reproductions alone: **6 passed**.
- Backend combined verification: **89 passed, 5 existing authorization xfails**.
  The authorization tester file and its strict markers are unchanged.
- Backend lint and `git diff --check` pass.
