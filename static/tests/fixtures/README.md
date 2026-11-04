# Test fixtures

`graph-plan/` is a frozen copy of the demo plan `destructive-commands` (a five-node graph:
logs, a Python step, a model step, a classifier and the report) with its transcripts,
expected verdicts and a matching `trust.json`. The tests use it instead of the repository's
`plan/`, which is the deployed plan and changes with every deploy. The tests sign it with
fresh keys of their own.

The tests check the box and the plan contract on this fixture; they never judge what a plan
decides.
