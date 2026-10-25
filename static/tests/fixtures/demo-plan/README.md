# The baked audit agreement

This folder is the dynamic part of [v0.5](../docs/AIAB_DESIGN_SPEC.md). The runtime
imports the signed `plan.py` in an isolated worker and calls `filter_logs`,
`build_query`, `classify`, `metric` and `emit_report`; `appeal` is not called in the
demo. The runtime, not the plan, owns inference credentials, query limits, output
validation and report signing.

The measured bundle consists of `__init__.py`, `plan.py`, `plan.json` and every
`prompts/*.txt` file. `signatures/lab.sig` and `auditor.sig` approve that exact
byte-and-path digest against static public keys. Tests and evaluation artifacts
are excluded from the executable digest; their hashes are committed in plan.json.
Docker bakes only executable inputs and signatures, not fixtures or private keys.

`plan.json` now uses **aiab-baked-plan/v1**. It is not a runtime registration request.
A changed agreement needs two signatures, a reproducible image build and an
attested redeployment. [Operations and exact commands](../docs/BAKED_PLAN_OPERATIONS.md).

`tests/evaluation.json` records the actual candidate deployment's synthetic
FP/FN evaluation. Demo signatures use locally controlled role keys. Both production
parties must independently review the source, metrics, prompts and static trust.

`demo-signed-plan.json` is preserved historical evidence for the older registered
JSON API; it is neither included in the image nor interpreted by this loader.
