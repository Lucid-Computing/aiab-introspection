# plan/

The dynamic part of AIAB: the audit logic both parties negotiate and sign. See section 3 of [docs/AIAB_DESIGN_SPEC.md](../docs/AIAB_DESIGN_SPEC.md).

Status: skeleton. Every file exists in its final shape, with the bio-research demo defaults; values marked `TODO` are placeholders to be agreed.

```
plan/
├── __init__.py
├── plan.py                 # the six functions: filter_logs, build_query, classify, metric, emit_report, appeal
├── plan.json               # agreed settings: scope, filter, model, classification, limits, output_schema
├── prompts/
│   └── bio_research_v1.txt # the classifier definition, pinned verbatim, referenced by prompt_id
├── tests/
│   ├── transcripts/*.jsonl # synthetic LogRecord files, one run per file; not part of the digest
│   └── expected.json       # expected verdict per file (and per session, for a later band answer)
└── signatures/             # lab.sig and auditor.sig, produced offline; not part of the digest
```

What the plan may do: `plan.py` imports only `dataclasses`, `typing` and `aiab.plan_api` (types fixed by the static part), has no network, file, clock or randomness access, and calls the model only through `run_llm`.

How a plan becomes the running plan: compute the plan digest, each party signs it offline, the PR carries the folder and both signatures, CI verifies the signatures and checks `plan.json` against the box's restrictions and merges, the build copies this folder into the image, the box re-verifies the signatures at boot. A plan is never edited in place; a new agreement is a new digest, two new signatures and a new image. This folder is copied into the image by `static/deploy/Dockerfile` and is therefore covered by the attestation; it is never mounted at runtime.

For the UI: the plan editor's blocks map to `plan.json` keys (`filter`, `window`, `model`, `classification`, `limits`, `output_schema`) and to the function bodies in `plan.py`; the dry-run screen reads the evaluation record produced from `tests/`; the sign screen collects the two files in `signatures/`.

Not yet available: the dry runner (`static/aiab/dryrun.py`), the digest and sign commands, and the runtime that executes the plan inside the box.
