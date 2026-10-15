# Baked-plan acceptance evidence

All records are committed synthetic fixtures. No private logger transcripts or
credentials are included.

- `candidate-v020-*`: signed evaluation-candidate deployment, its attested release
  journal and actual inference results. This candidate had explicitly unfilled
  evaluation commitments. Its measured evaluation was committed to the final
  `plan/plan.json`, then the complete bundle was signed again by both demo roles.
- `final-v021-*`: successful deployment and repeated real inference of that final
  agreement. TP=2, TN=1, FP=0, FN=0, errors=0; FPR denominator 1, FNR denominator 2.
  These are smoke-test counts, not general classification accuracy.
- `cli-v021-*`: documented command-line submission, report retrieval, identity pin
  and successful offline verification with socket creation disabled. The report is
  from an additional real benign fixture run.

The acceptance driver checks the image and baked-plan pins, disabled registration,
plan/text/signature tampering, unauthorized access, per-boot idempotency and signed
schema-valid reports. Local tests additionally cover full-query context preflight,
worker filesystem/network/process escape attempts, timeout cleanup, invalid output,
source-signature tampering, scope violations and privileged CI validation.

Public anchor copies demonstrate the test; they are not automatically trusted
roots for a new verifier. An auditor independently pins an identity through the
attested connection. Preserve the matching released plan folder and release file
when verifying historical reports. The old candidates can be reproduced from the
source commit recorded in each release artifact.

A successful demo with signed synthetic exports is not acceptance of native logger
proofs/checkpoints. The UI, verified wipe, agents, appeals and multiple-model policy
remain part of the broader requirements, not claims of these artifacts.

`final-v022-*` is the current hardened runtime release: named API imports only,
fresh attestation and repeated successful live acceptance. CI passed 589 tests
with one skipped, and the two clean image builds produced identical digests.
The final plan and its evaluation commitments are unchanged from v0.2.1.
