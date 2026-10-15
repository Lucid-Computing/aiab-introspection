# Operator tooling acceptance

These artifacts use only the committed synthetic demo transcripts. The reusable
`auditor.evaluate` command ran the actual jointly signed plan against the live
v0.1.1 Tinfoil service: TP=2, TN=1, FP=0, FN=0, errors=0. It recorded signed results,
input commitments, decision/dataset commitments and end-to-end observed durations.
Those durations include network/polling and are not a standalone model benchmark.
`final-unsigned.json` is the output of `prepare-plan`; it commits to this evaluation
without retaining stale signatures. It still needs two signatures before admission.

`anchor.json` was acquired through the independently pinned live attested connection.
`bundle.json` was exported by the new CLI; `offline-verification.json` records its
subsequent verification with no service access. This public copy is evidence of the
test, not a trust root that a new verifier should automatically approve. Independently
pin your own anchor. The bundle does not contain portable hardware endorsement of
the report key.

The visualization uses the earlier recorded five-case acceptance evidence; these
additional files demonstrate the reusable operator commands. See
[operator instructions](../../../../docs/AUDIT_OPERATIONS.md).

`offline-after-redeploy.json` records re-verification of the old boot report after a new boot was observed, with Python socket creation disabled during verification.

`release-v013-attempt.json` records the successful automated build, reproducibility check, measurement, deployment and attested readiness check. `release-v013-acceptance.json` records the five-case live synthetic acceptance run against that release. The CPU enclave boot is `3c2e4e1a8323460c96e75f4212130597`.
