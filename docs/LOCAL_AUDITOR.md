# Local auditor

With a local AIAB listening on port 8443, run:

```sh
uv run auditor verify --host localhost --insecure-local
uv run auditor report --host localhost --insecure-local
uv run auditor rollup --host https://localhost:8443 --insecure-local \
  --state-file /tmp/aiab-auditor-checkpoint.json
```

The local flag trusts a self-signed TLS certificate and skips enclave attestation.
It permits only the exact hostnames `localhost` and `127.0.0.1`, with an optional
HTTPS scheme and port; the default port is 8443. HTTP URLs, credentials, paths,
other hosts, redirects, and environment proxy routing are rejected or disabled.
The warning is intentional: an endpoint impersonator can replace both a report
and its public key. Signatures alone do not establish the application's identity.

All commands check the public identity against the canonical manifest digest.
Passing `--expected-policy-digest` (or `EXPECTED_POLICY_DIGEST`) also compares the
policy to a separately approved digest. `verify` prints the policy digest and says
the image is unverified. Outside local mode, the auditor requires approved Tinfoil
release and policy digests, invokes the Tinfoil CLI and fetches artifacts through
the SDK's verified TLS transport. See the
[attested deployment procedure](../deploy/tinfoil/README.md). An expected release
digest cannot be verified in local mode.

`report` and `rollup` validate signatures, exact report fields, finalized counters,
boot and policy consistency, classifier and enforcement mode, period duration,
sequence ordering, and period ordering. Repeated identical reports are counted
once; conflicting reports for the same sequence fail verification. Fetching the
public identity before and after the other artifacts detects a restart during a
fetch. Retry the command after such a failure.

Periods become available only after their end plus the processing allowance. A
fresh boot therefore initially has no finalized reports. Rollups include counts
for each unique fetched period and totals within that boot and policy. `N/A` means
no classifications succeeded. Request shares are descriptive counts at this
proxy, not token or compute shares, research prevalence, or population estimates.

The optional checkpoint stores only the last verified AIAB id, boot id, public
key, policy digest, sequence, and period end in a small JSON file with private
permissions. It contains no conversation bodies or report history. It is replaced
atomically only after all fetched artifacts validate; an empty history or a
backward same-boot history cannot erase a newer checkpoint. First use reports
that earlier continuity is unknown. Subsequent runs warn about changed identity,
boot, key, or policy, missing sequences, time gaps, receded history, and reports
overdue against the auditor's local clock and the policy schedule.

These warnings indicate incomplete evidence, not malicious behavior. Retention,
restarts, downtime, and clock differences can explain gaps. The checkpoint cannot
recover missing reports, detect every fork, or discover restarts this auditor
never observed. Use a separate checkpoint per independently monitored appliance;
the file is intended for one auditor invocation at a time.
