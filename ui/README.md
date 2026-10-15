# Agreement UI (separate workstream)

The UI owns drafting, review, preview, offline signature collection and run/report
presentation. It remains outside the enclave image. The active target is section 4
of [v0.5](../docs/AIAB_DESIGN_SPEC.md); see [operations](../docs/BAKED_PLAN_OPERATIONS.md)
for the actual schemas and commands.

The plan editor changes `plan/plan.json`, `plan/plan.py` and prompt files. Both
parties sign the exact folder digest offline using `python -m auditor.baked`;
private keys never go to the UI, CI or box. The PR contains the bundle and both
signature files. The static verifier checks signatures and restrictions, then the
release builds the bundle into the image. Do not call `POST /aiab/plans`: runtime
registration is disabled in the baked service.

Read `GET /aiab/manifest` for restrictions, plan/image commitments, model binding
and output capacity; verify these through the attested client against the approved
release. Submission is `POST /aiab/runs` with complete records, scope and authenticated
source evidence. Poll `GET /aiab/runs/{run_id}/report` and verify the v3 signed
envelope. A display of a server-supplied hash alone is not attestation verification.

The recorded run visualization is historical explanatory material, not this UI.
No plan maker is implemented in this folder yet. UI integration remains required
for the full system, after the v0.5 CLI-first milestone.
