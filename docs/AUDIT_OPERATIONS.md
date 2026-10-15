# Audit operator tooling

Run commands from `static/` after `uv sync --locked`. All output paths must be new.
Use the current `deploy/tinfoil/audit/release.json` for host/repo/release/policy pins.
Never approve pins merely because a remote server supplies them.

## Offline verification

First, while connected, independently approve the release/policy pins and save the
identity with `aiab-plan pin-identity`. This uses the same verified Tinfoil transport
as report retrieval:

```sh
uv run aiab-plan pin-identity --host HOST --repo OWNER/REPO \
  --release-digest sha256:RELEASE --policy-digest sha256:POLICY \
  --token-file /private/auditor.token --output /private/identity-anchor.json
uv run aiab-plan export-bundle --host HOST --repo OWNER/REPO \
  --release-digest sha256:RELEASE --policy-digest sha256:POLICY \
  --token-file /private/auditor.token --plan ../plan/demo-signed-plan.json \
  --run-id RUN --input-commitment sha256:INPUT --output /private/bundle.json
```

Then disconnect or shut down the service. The following command uses no network:

```sh
uv run --offline aiab-plan verify-offline --bundle /private/bundle.json \
  --anchor /private/identity-anchor.json --run-id RUN \
  --plan-digest sha256:PLAN --input-commitment sha256:INPUT
```

Protect the anchor independently of the bundle. The verifier checks both party
signatures against the manifest authenticated by that anchor, the report signature,
run/input/plan/boot/policy bindings and the result schema. A valid signed failure is
reported as a verified **failed report**, never a clean classification. Changing the
bundle's identity cannot introduce a new trust root.

This is offline verification against an already trusted key. It is not a portable
hardware attestation package for a new auditor who does not trust the saved anchor.
A historical anchor says nothing about today's deployment. Tinfoil's newer
[attested keys](https://docs.tinfoil.sh/containers/attested-keys.md) could provide
stronger portable key provenance but require V3 attestation; that migration is not
part of this implementation. Public verification summaries are not cryptographic
substitutes for either a trusted anchor or raw verifiable endorsements.

`aiab-plan schema-info --plan SIGNED_PLAN` computes conservative result-only capacity
from the schema. The same derived `egress_max_bits` appears in offline verification
output. This preserves the fixed v2 report format and its signature compatibility.

## Reusable evaluation and final signing

Prepare a private JSON array of `{id, text, expected}` cases. `expected` is the full
result value, such as `{"flagged":true}`. The dataset is limited to 100 unique cases
per invocation; deployment retention still applies. Labels and text are synthetic
unless you separately authorize real transcripts for hosted inference.

```sh
PYTHONPATH=. uv run python -m auditor.evaluate \
  --host HOST --repo OWNER/REPO --release-digest sha256:RELEASE \
  --policy-digest sha256:POLICY --token-file /private/lab.token \
  --logger-key /private/logger.key --plan /private/candidate-signed.json \
  --dataset /private/dataset.json --run-prefix eval-unique \
  --output /private/evaluation.json
uv run aiab-plan prepare-plan --plan /private/candidate-signed.json \
  --evaluation /private/evaluation.json --output /private/final-unsigned.json
```

Custom binary result schemas use `--positive '{"decision":"flag"}'` and
`--negative '{"decision":"clear"}'`. Both labels must satisfy the signed schema.
Input text and case names are not copied to evaluation output, but constrained
results, labels and hashes remain potentially sensitive: keep that artifact private.
Each case records a signed report, input commitment, outcome and observed duration.
Errors are counted separately, not silently treated as true negatives. FPR/FNR have
explicit denominators and exclude errors. A zero denominator is undefined.

`prepare-plan` refuses incomplete/errored evaluation or a changed decision configuration.
It strips old signatures by emitting an unsigned plan. Both parties must review and
sign this final artifact; no tool signs for them. It does not enforce an accuracy
threshold: that decision belongs to the agreement. Evaluation input/labels can be
wrong; signing their hash does not prove scientific validity.

## Automated release

Commit/push the chosen source revision first. Run from a clean checkout, using the
existing `gh` login and `/home/jon/.tinfoil/config.json` admin credential. Credentials
are never passed in command arguments, placed in Git, or uploaded to CI. The
coordinator is local; image construction and measurement execute on GitHub runners.

```sh
PYTHONPATH=. uv run python deploy/tinfoil/audit/release.py \
  --source-ref feat/signed-plan-audit-execution --tag v0.1.4 \
  --key-directory /home/jon/.local/share/aiab-audit-demo \
  --output /private/release-plan
# Executes the complete selected release; choose an unused tag/output directory.
PYTHONPATH=. uv run python deploy/tinfoil/audit/release.py \
  --source-ref feat/signed-plan-audit-execution --tag v0.1.4 \
  --key-directory /home/jon/.local/share/aiab-audit-demo \
  --output /private/release-attempt --execute
```

The coordinator requires local HEAD to match the requested remote ref, verifies the
existing instance matches the baseline, requires successful CI and matching clean
build digests, checks source/config commit identity, publishes config with an atomic
git push, measures the tagged config, updates the instance, verifies expected live
release/policy and runs synthetic acceptance. `attempt.json` records stages and the
previous/target release; success is recorded only at the end. New release files are
written into the attempt directory for review and then committed back to the source
repository; they are not silently written into an independently edited checkout.

After failure, inspect the last stage, CI run and Tinfoil instance before retrying.
Do not blindly reuse a tag or submission ID. In-flight updates can be inspected or
cancelled through the provider; once promoted, reverting is a new explicit update to
the previously approved tag, followed by independent verification. Restart loses run
state; saved bundles remain verifiable against their prior identity anchors. Keep the
previous config/release and archive important reports before starting an update.

The release config repository uses JSON syntax in its YAML file. The coordinator
preserves secret-name bindings and never reads their values from Tinfoil. It does
not automate cross-party approval, source-access grants, provider account setup, or
independent security review.


### Release identity and unchanged application images

The coordinator writes `AIAB_RELEASE_TAG` into the measured deployment configuration.
This makes the release measurement distinct even when the application image and
policy are unchanged. Without it, two release tags can share a digest; the SDK's
Sigstore lookup may retrieve the earlier tag's certificate and correctly reject it
against the newly selected tag. We observed this on v0.1.2 and retained the failure
rather than relaxing tag verification. Recovery publishes a fresh tagged config;
it never edits a published tag or disables signature checks.
