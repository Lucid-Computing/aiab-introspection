# Independent workbench testing and fixes

Three Sol testers examined authoring, execution and authorization in separate
worktrees on November 6, 2026. They found nine defects. All nine have fixes and
regression coverage, deployed in `0dd3cecbbb2a15e2c4f2a16d3a40dc3db1d6794a` at
https://aiab-dev-astra-one.onrender.com/workbench.

- [Execution report](WORKBENCH_EXECUTION_TEST_REPORT.md): contradictory relational
  observations now abstain independently of record order; historical consent uses
  the latest exact-bound state before admission; unrelated review versions abstain.
- [Authorization report](WORKBENCH_AUTHORIZATION_TEST_REPORT.md): stable OIDC
  issuer/subject identities and a separate local namespace replace ambiguous
  usernames; password login requires exact Origin; HTTPS auth cookies are Secure;
  private copies no longer hide the original shared agreement.
- [Builder report](WORKBENCH_BUILDER_TEST_REPORT.md): runs and evaluations require
  the checked digest and queue its immutable snapshot; unapplied code/settings
  buffers survive view changes and require explicit conflict/discard decisions.

The individual reports retain their original failing observations and later
resolution notes. All corresponding expected-failure markers are removed in the
current tests. Frontend verification passed 145 tests, typechecking and build.
Execution regression verification passed 38 tests, including checks across all
432 synthetic cases. Integrated login/authorization verification passed 47 tests.
These suites overlap with broader tests; their counts should not be added as a
single independent coverage number.

[Hosted regression evidence](hosted-regression-checks.json) records successful
rejection of missing/stale execution digests and successful rehearsal/evaluation
on the exact requested digest. The 16-case deterministic evaluation matched all
selected synthetic outcomes. Hosted login checks rejected missing/foreign Origins,
confirmed Secure session/pending-login cookies, and returned the 27-entry catalog
under the new principal namespace. One first login check during the final rollout
failed; a subsequent direct check returned 303 and the full regression check passed.
This is not a browser-human usability study, independent real-world accuracy
evaluation, or confidential supervisor attestation.

The identity transition rejects old sessions and quarantines old workbench storage
without rewriting it. Users must log in again. Historical ownership, enrollments,
consents and release accounting require independent reconciliation before importing
authorized unsigned material into the new namespace; see
[the migration procedure](../OIDC_AUTH_MIGRATION.md#auth-010203-security-transition-session-protocol-v2).
Fresh synthetic rehearsals need no legacy-data migration. Confidential employee
audits remain disabled on the ordinary Render host.
