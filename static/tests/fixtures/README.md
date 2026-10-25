# Test fixtures

`demo-plan/` is a frozen copy of the original demo plan (bio-demo-001) with its
transcripts, expected verdicts, demo signatures and the matching `trust.json`. The tests use it
instead of the repository's `plan/`, which is the deployed plan and changes with every deploy.

The tests check the box and the plan contract on this fixture; they never judge what a plan decides.
