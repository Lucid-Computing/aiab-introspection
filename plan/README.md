# plan/

The dynamic part of AIAB: the audit logic both parties negotiate and sign. See section 3 of [docs/AIAB_DESIGN_SPEC.md](../docs/AIAB_DESIGN_SPEC.md).

Target layout:

```
plan/
├── __init__.py
├── plan.py            # the six functions: filter_logs, build_query, classify, metric, emit_report, appeal
├── plan.json          # agreed settings: model digest, prompt id, threshold, limits, output schema
├── prompts/           # prompt files, pinned verbatim, referenced by prompt_id
├── tests/             # synthetic transcripts and expected labels for the dry run; not part of the digest
└── signatures/        # lab.sig and auditor.sig: Ed25519 signatures over the plan digest
```

How a plan becomes the running plan: compute the plan digest (hash of this folder without `tests/` and `signatures/`), each party signs it offline, the PR carries the folder and both signatures, CI verifies the signatures and the restrictions and merges, the build copies this folder into the image, the box re-verifies the signatures at boot. A plan is never edited in place; a new agreement is a new digest, two new signatures and a new image.

This folder is copied into the image by `static/deploy/Dockerfile` and is therefore covered by the attestation. It is never mounted at runtime.
