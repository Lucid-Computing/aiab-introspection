# Signed audit plans

The current baseline is one JSON file containing the plan and both Ed25519 signatures. See [the implemented contract](../docs/AUDIT_SERVICE.md) and [plan schema](../schemas/plan-v1.schema.json).

[demo-signed-plan.json](demo-signed-plan.json) is the actual synthetic-demo agreement exercised against the v0.1.1 Tinfoil release. It includes the program, prompt, examples, model and image pins, scope, evaluation commitments, output schema and limits. Its two signing roles use locally provisioned demo identities. The corresponding real inference results and verification evidence are in [static/demo/audit/evidence](../static/demo/audit/evidence).

Build the generic executor first; sign a plan referring to its image digest; then register the signed file with `POST /aiab/plans`. The file is data and is not baked back into the referenced image. Any plan change requires two new signatures and yields a new plan digest, but need not rebuild the executor. Unsupported language/model changes do require an executor release.

The earlier six-function/folder-based proposal is a possible future extension. The implemented baseline has one `run(text, model, policy)` program and a separately configurable output schema, with no appeals or separate report program. Private signing keys never enter the image or repository.
