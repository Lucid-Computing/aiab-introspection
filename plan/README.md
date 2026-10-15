# Signed audit plans

This folder contains the deployed signed-JSON example and a separate six-function
plan skeleton merged from master. They are different contracts; the skeleton is
not yet executable through the deployed API.

The deployed baseline is one JSON file containing the plan and both Ed25519
signatures. See [the implemented contract](../docs/AUDIT_SERVICE.md) and
[plan schema](../schemas/plan-v1.schema.json).

[demo-signed-plan.json](demo-signed-plan.json) is the actual synthetic-demo
agreement exercised against the v0.1.1 Tinfoil release. It includes the program,
prompt, examples, model and image pins, scope, evaluation commitments, output schema
and limits. Its two signing roles use locally provisioned demo identities. Real
inference results and verification evidence are in
[static/demo/audit/evidence](../static/demo/audit/evidence).

Build the generic executor first; sign a plan referring to its image digest; then
register the signed file with `POST /aiab/plans`. The file is data and is not baked
back into the referenced image. Any plan change requires two new signatures and a
new plan digest, but need not rebuild the executor. Unsupported language/model
changes do require an executor release. Private signing keys never enter the image
or repository.

## Six-function skeleton from master

The skeleton follows section 3 of the
[archived v0.5 proposal](../docs/archive/AIAB_DESIGN_SPEC_v0.5.md).
It contains bio-research demo defaults; `TODO` values remain placeholders.

```text
plan/
├── demo-signed-plan.json   # deployed signed-JSON example
├── __init__.py
├── plan.py                # filter_logs, build_query, classify, metric, emit_report, appeal
├── plan.json              # proposed folder-based settings, not an API registration document
├── prompts/
│   └── bio_research_v1.txt
├── tests/
│   ├── transcripts/*.jsonl
│   └── expected.json
└── signatures/            # proposed offline signature files; no private keys
```

`static/aiab/plan_api.py` provides the skeleton's types and model-call hook. Its
local tests install a fake classifier. `appeal` is unimplemented, and no production
runtime or sandbox executes this skeleton. The module's limited imports are a
coding convention, not a security boundary.

Although `plan.json` also says `aiab-plan/v1`, it has the older folder-based shape
and does **not** conform to the deployed plan schema. Do not submit it to
`POST /aiab/plans`; use the signed-JSON example and signing CLI for that endpoint.

The archived proposal describes signing a folder digest, automatic PR merging,
baking the folder into an image, and boot-time verification. That workflow is not
implemented here: the Dockerfile does not copy `plan/` into the image. The existing
CLI signing, evaluation and runtime registration commands serve the signed-JSON
contract, not this folder contract.

For the separately owned UI, the skeleton offers proposed editor blocks for
filtering, windows, classification and reporting. Connect the UI to the documented
live API until an extended contract is agreed and implemented. See the
[full requirements audit](../docs/REQUIREMENTS_COMPLIANCE.md) for outstanding work.
