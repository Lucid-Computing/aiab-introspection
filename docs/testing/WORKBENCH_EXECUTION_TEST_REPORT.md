# Workbench execution tester report

Date: 2026-11-06 (UTC). Tester: Sol, isolated `t3-f9f437e8` worktree.
Source baseline: `f3aec71`; hosted baseline supplied by coordinator: `e404703`
(not independently established from a deployment metadata endpoint).
Read `docs/WORKBENCH_DEMO.md`, `docs/WORKBENCH_IMPLEMENTATION_CONTRACT.md`,
contracts, catalog, analyzer, evidence, broker, runner, run API and evaluation implementation.
No application fixes, pushes, deployments, provider changes or other-worktree writes.

## Prioritized findings

### EXE-01 — P1: contradictory relational assignment records change verdict with input order

Location: `static/aiab/workbench/analyzers.py:263` (dictionary comprehension),
`:270` (decision), `:763` (predicate converted to supported/no_finding).

Reproduction: generate seed 93; select the first supportable positive
`retaliation-indicators` investigation using its separate truth ledger in the test
process. Its raised-cohort after-period value is 2 and before/control values are 10.
Append an independently identified assignment with the same `(cohort, period)` but
`access_units=10`. Give it a unique event and native ID, recompute its synthetic
commitments and update/reseal the collector inventory. Evaluate the same records
in forward and reverse order.

Expected: ambiguous conflicting relational observations require
`insufficient_evidence` in either order, absent adjudication or a declared aggregation.
Actual: forward gives `no_finding`, reverse gives `supported`; the last record wins.
Forward details: `difference_in_differences=0`, `causal_claim=false`,
`coverage_assertion="complete synthetic collector inventory"`.

Evidence: `test_contradictory_assignment_is_order_independent_abstention` fails with
`AssertionError: ['no_finding', 'supported']` when run with `--runxfail`.
This is a method correctness defect, not an admission bypass. It can conceal a
recorded discrepancy or assert one solely from ingestion order. Distinct native
IDs deliberately avoid the already-working conflicting-native-version gate.

### EXE-02 — P1: historical consent audit ignores a later revocation

Location: `static/aiab/workbench/analyzers.py:539`, especially `:548`–`:554`.

Reproduction: select the first seed-93 `consent-scope` case with expected
`no_finding`. Find the actor's valid approval. Append a uniquely identified consent
record bound to the same plan/data/purpose/recipient/person, with `revoked=true`
and timestamp one second before admission, later than the original approval. Recompute
synthetic commitments and collector counts. Evaluate.

Expected: the earlier approval cannot establish valid consent at admission after
its revocation; return `supported` for the recorded executed violation or abstain
pending adjudication. Actual: `no_finding`, with `required_approvals=1`,
`valid_approvals=1`, `personal_scope_valid=true`. The loop only adds approvals and
never removes a revoked person's prior approval.

Evidence: `test_consent_revocation_invalidates_prior_approval` fails with
`outcome='no_finding'` under `--runxfail`. This affects the substantive historical
consent analyzer only. No claim is made that GovernanceStore admits revoked users;
that separate enforcement path was not bypassed or changed.

### EXE-03 — P2: an unrelated review version is treated as no discrepancy

Location: `static/aiab/workbench/analyzers.py:130`–`:140`.

Reproduction: select seed-93 first positive `review-quality` investigation. Replace
only the review's `diff_hash` with `unrelated-version`, reseal its synthetic
commitment and evaluate. Defect and release still refer to the original version;
collector record counts remain consistent.

Expected: `insufficient_evidence` for review quality of the released version,
because the required version-specific review is unavailable. Actual: `no_finding`,
`version_matched=false`, `detected=false`, despite a confirmed defect on the
released version. `relevant=false` is converted directly to a negative predicate.

Evidence: `test_unmatched_review_version_abstains` fails with
`outcome='no_finding'` under `--runxfail`. The catalog instructs preservation of
insufficient evidence for missing/conflicting evidence. A different version is
not evidence that the released version's review found no discrepancy.

## Reproducible local checks

Owned regression file: `static/tests/workbench/test_tester_execution.py`.
Only the three reported defects are marked strict xfail; unexpected repairs become
XPASS failures so the markers must then be removed. To expose the failures:

```sh
uv run --project static pytest static/tests/workbench/test_tester_execution.py \
  --runxfail -k 'contradictory_assignment or consent_revocation or unmatched_review' \
  --tb=short -q
```

Observed on the final regression file: **3 failed, 31 deselected** in 3.93 seconds.
The exact failed assertions are preserved above and in the executable tests.

Normal targeted command:

```sh
uv run --project static pytest static/tests/workbench/test_tester_execution.py -q -rx
uv run --project static ruff check static/tests/workbench/test_tester_execution.py
```

Observed: **31 passed, 3 strict xfailed** in 13.47 seconds on the final run; Ruff passed.

Checks cover all 27 substantive methods at independently selected seed 93:
supportable baseline, missing record with intact inventory, a malformed string
collector boolean, a deleted decisive field with valid commitments, and
contradictory native bytes with consistent inventory. These all abstain as expected.
The scoring check verifies all 432 supportable synthetic outcomes and that removing
all predictions retains the 432 missing-prediction denominator and all expected
positives in recall, with undefined precision at zero predicted positives.
The new runtime checks cover cancellation between deterministic steps (no partial
result), exact temporal join at zero seconds, unauthorized evidence IDs, unknown
join keys not matching, Cartesian join budget and oversized response rejection
without truncation.

Additional existing real sandbox/Pi/evidence tests were run with:

```sh
uv run --project static pytest static/tests/workbench/test_runtime_integration.py \
  static/tests/workbench/test_runtime.py static/tests/workbench/test_evidence.py -q
```

Observed: **157 passed in 300.88 seconds**.

These use actual local Pi/OS sandbox execution and explicitly labeled inference
doubles, not live inference. They include all 27 catalog deterministic executions,
step/subject scope, reauthorization, token/tool/turn limits, deadlines,
cancellation/child reaping, SQL resource limits and private projection checks.
Validated `isolate.node` and `node_modules` were copied read-only from the authorized
parent worktree into this worktree's ignored build paths. Python dependencies were
installed by the locked uv project environment. No attestation was bypassed.

## Hosted HTTP evidence

Base: `https://aiab-dev-astra-one.onrender.com`. Login used the dedicated local
credential file only inside scripts, form `POST /login`, then same-origin `Origin`
headers on mutations. No credentials, cookies or provider payloads were printed.
Bootstrap returned 27 catalog entries. No browser tools were used.

Created only this uniquely named test-owned plan, copied from the actual hosted
`retaliation-indicators` catalog template and explicitly reduced to its rules step:

- Plan: `tester-execution-1a41da3c07314adf86383fb35b8f373c`.
- Saved digest: `a6e5fda21f7e4327112153598b86ec6e22ce8f638687acfcd9066aa885366083`.
- Rehearsal: `378207cbc12c4cd5a3d959ee0d53fece`.
- Run creation HTTP 200; final status `completed`; 16 findings; zero turns,
  tool calls and model tokens; no run error.
- Fetching a finding's cited source via
  `GET /api/workbench/runs/{run_id}/evidence/{event_id}` returned HTTP 200 and
  the exact cited event ID. A nonexistent/uncited event returned HTTP 404.
- Deterministic evaluation, seed 0, case cap 16:
  `c82ba08f508e467db55bd49a332ae198`, final status `completed`.
  Metrics: 16 evaluated, 16 exact matches, 10 abstentions, zero unknowns,
  zero citation errors; TP=2, TN=4, FP=0, FN=0;
  accuracy denominator 16, precision denominator 2, recall denominator 2.
  This is synthetic development coverage, not real-world accuracy.

A deliberate alternate-seed evaluation of this same fixed-scope plan used seed 93:
`0fcee24aaccf46e3903b4df84585923b`. It completed with 16 unknowns,
zero evaluated, accuracy 0/16, recall 0/2 and undefined precision (denominator 0).
Each case records `failure=execution_failed`. The catalog plan retains seed-0
subject IDs while newly generated seed-93 records have different subjects. The
runtime intersects those exact scopes instead of widening them. This is the
**documented exact-plan-scope gate**, not a fourth correctness finding; denominators
correctly include all selected failures. A new evaluation at matching seed 0 above
confirms the distinction. Generic per-case failure text limits hosted diagnosis.

## Limits and retained artifacts

No live provider call was made; live effectiveness, provider cancellation and
current attestation were not independently qualified. The optional live case was
omitted because deterministic method defects were already reproducible without
inference. Hosted execution tested the standard synthetic fixture, not mutated
custom imports (rehearsal intentionally permits only built-in Northstar).
The malformed/conflicting-record findings are established locally on the same
source baseline, not claimed as hosted live-agent results.

Confidential Render execution/admission, imported-data consent and recipient
release are documented gates and were not bypassed. Synthetic normalized hashes
are not native source authenticity. Existing demo plans and all services/settings
were left unchanged. The unique hosted plan, run and both evaluations above are
retained intentionally as reproducible test evidence; no existing objects were
overwritten. This report and its regression file are the only tracked changes.
