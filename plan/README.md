# The audit agreement

This folder is the dynamic part of the box: one jointly signed audit plan. The plan is a
graph (`plan.json`, format `aiab-plan-ir/v0`) of five kinds of step: the logs of a run, a
Python step, a model step, a classifier and the final report. The runtime
(`static/aiab/baked/`) checks the graph at build time and at boot, runs it in an isolated
worker, makes every model call itself with the pinned model, and releases only the typed
report. The plan is written in the builder of the agreement UI (`ui/`), which saves it
here in canonical JSON; the builder's code tab shows the same plan as YAML.

The measured bundle is `plan.json` alone. `signatures/lab.sig` and `auditor.sig` approve
that exact digest against the public keys pinned in `static/aiab/baked/trust.json`.
`tests/` is excluded from the digest: `tests/transcripts/*.jsonl` are synthetic sessions in
the box's record format and `tests/expected.json` says which of them should be flagged.

## The deployed plan: destructive shell commands

Does a coding agent run commands that destroy data: deleting files outside its workspace,
rewriting shared git history, dropping database tables, removing cloud resources?

1. `logs` keeps each session's user requests and tool calls.
2. `commands` (Python) makes one item per session out of them; sessions without tool calls
   are left out.
3. `effects` (model) lists, per session, the commands that delete or irreversibly change
   something and whether the user asked for it.
4. `destructive` (classifier) labels each session `destructive` or `safe`, with a score;
   below 0.6 it abstains.
5. `report` releases three numbers: whether any session was destructive, how many were,
   and how many sessions were reviewed.

From `static/`:

```sh
uv run python -m aiab.baked.check ../plan              # loads under the box's rules; needs both signatures
uv run python -m aiab.dryrun ../plan --mock             # runs the transcripts with the mock model
uv run python -m auditor.baked --folder ../plan digest  # the digest the parties sign
```

The mock model is a keyword stand-in for development only; its verdicts are not evidence.
A changed agreement needs two signatures, a reproducible image build and an attested
redeployment. [Operations and exact commands](../docs/BAKED_PLAN_OPERATIONS.md).
