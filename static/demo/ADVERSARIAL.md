# Adversarial demonstration

`bash demo/adversarial.sh` safely narrates all six scenarios without running any
commands. It works without `uv`, Docker, a model or credentials. Bash 4 or later
is required. `--stage tamper|debug|override|bypass|policy|restart` selects one case.

The driver is operator-assisted: it runs the probes, while an operator prepares
and restores disposable deployments during explicit pauses. There is no approved
public release or hostname in this repository. Follow the existing
[release/deployment procedure](../deploy/tinfoil/README.md) first. Do not publish
unreviewed source or use real conversation data for this demonstration.

The only currently runnable scenario without a deployment is the real startup
configuration guard:

```sh
UV_PYTHON=3.12 bash demo/adversarial.sh --execute --stage override
```

It calls the same `Config.from_env` path used at startup, supplying only the
unsupported override. It does not launch a server or contact the supplied URL,
and prints `CLASSIFIER_ENDPOINT_OVERRIDE is unsupported`. This establishes the
configuration rejection, not any TEE property.

## Execute against an approved deployment

Install locked dependencies and the reviewed Tinfoil CLI. Begin every stage with
the original approved, non-debug deployment. The driver verifies that baseline
before prompting for changes. Each pause requires typing `ready` after the
operator completes its instructions; a piped acknowledgment is rejected.

Replace the uppercase public placeholders in this command:

```sh
UV_PYTHON=3.12 bash demo/adversarial.sh --execute --stage tamper \
  --host HOSTNAME --repo OWNER/REPO \
  --expected-digest APPROVED_TINFOIL_RELEASE_DIGEST \
  --expected-policy-digest APPROVED_POLICY_DIGEST
```

These are the auditor's exact flags. The release digest is the 64 lowercase hex
`tinfoil.hash`/Sigstore subject, distinct from the `sha256:...` OCI image digest.
Supply independently approved values; never approve the values merely observed
at a changed endpoint. No key belongs in these arguments. The script does not
load `.env`, print arguments or raw captured probe output, execute command strings,
or call deployment management commands. It keeps captured audit/generator output
in memory; only the explicitly selected auditor checkpoint is written to disk.
Do not run with shell tracing or verbose shell input enabled.

Each live stage needs the baseline flags above, plus the following preparation:

| Stage | Preparation and expected result |
|---|---|
| `tamper` | In a disposable checkout, change one byte of `classifiers/ai_capabilities.md`, rebuild, and deploy its measured release at the selected target. Verification against the original pin must fail specifically on release digest mismatch. |
| `debug` | Provision a debug enclave using the supported platform workflow and independently confirm its debug hardware evidence. The verifier must reject it. The driver cannot distinguish debug-policy rejection from every generic CLI failure, so its output explicitly retains that limit. |
| `override` | Local configuration probe only; no deployment or baseline flags required. |
| `bypass` | Add `--inference-url https://DIRECT-INFERENCE-HOST`, independently known to reach inference without this proxy. Quiesce other proxy traffic and let its admitted requests finalize first. The driver sends three synthetic requests and requires three HTTP successes; after another finalized period, retained same-boot proxy counts must be unchanged. |
| `policy` | Add `--candidate-release-digest INDEPENDENTLY_APPROVED_CANDIDATE_RELEASE_DIGEST`. Change only `BLOCK_THRESHOLD` and restart, keeping the exact OCI image. Independently review candidate release/config/image evidence, retaining the old policy pin. The required failure is policy digest mismatch. |
| `restart` | Add `--state-file /tmp/aiab-adversarial-checkpoint.json` using a new path. Save a verified finalized baseline, restart with the original approved release/settings, then wait for a new finalized report. Require the auditor's changed-boot continuity warning. The checkpoint remains available after the script exits. |

The current upstream verifier selects the repository's latest release. Therefore
the approved baseline, changed release and restored release must each be the
release selected by that verifier at their corresponding pause. Plan isolated test
repositories/releases accordingly; simply changing a running container's tag may
not restore the verifier's selected release. Debug provisioning can require a
platform workflow or disposable target that this repository does not define.
If a stage cannot be prepared at the selected hostname, stop and record that
scenario as unperformed instead of substituting a local fake attestation.

A policy-only environment change in measured YAML can also change the release
digest. The candidate release pin isolates the effective-policy check from that
release change; it does not waive the independent review or assert the image
matches without release evidence. After a failing probe, restore approved source,
release, settings and non-debug mode. The final pause verifies the original pins
again before the stage completes. An abort prints a restoration reminder and
cannot automatically undo operator provisioning.

For bypass/restart, wait through the configured report period plus the fixed
30-second finalization allowance. The driver prompts instead of guessing the
deployment's period or sleeping for an unbounded interval. Bypass rejects changed
boots, missing baseline periods, unsuccessful direct traffic, unchanged period
counts, and nonzero proxy count changes as inconclusive. Independent routing
knowledge and generator counts supply demo ground truth; the auditor cannot
discover missing bypass traffic alone. Restart warnings describe incomplete
evidence, not malicious behavior, and cannot recover lost reports or unseen forks.

Dry-run and startup-guard tests run under `make check`, alongside shell syntax
validation. They do not establish live tamper/debug/bypass/policy/restart results.
See the [threat model](../docs/THREAT_MODEL.md) for classifier robustness, provider
and hardware trust, plaintext exposure, exact-count leakage and output channels.
