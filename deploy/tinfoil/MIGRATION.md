# Reviewed enclave migration preparation

This is a local release candidate and runbook, not evidence of a live enclave.
The ordinary Docker service at `jonathan.quest/aiab-introspection` remains unchanged.

## Cost and publication decisions (2026-09-18)

The [Containers pricing page](https://tinfoil.sh/pricing) lists a $20 monthly base,
$0.06 per CPU core-hour, and $0.01 per GB-hour. The minimum
[configuration](https://docs.tinfoil.sh/containers/configuration) is 2 cores and
8192 MB: $0.20 per running hour, or **$166/month** at 730 hours, plus model usage.
Ten running hours in a month cost $22 including the base. Confirm billing start,
deletion semantics, taxes and account-specific terms before creating a container.

Tinfoil needs a public measured configuration repository. Keep the existing source
repository private; `prepare_release.py` emits an allowlisted separate candidate.
The image contains Python application source and classifier definitions. Public
image publication exposes those files even if the Git repository stays private.
[Private registry authentication](https://docs.tinfoil.sh/containers/private-images)
requires Enterprise; its additional price and this account's entitlement are
unconfirmed. Choose and approve image visibility before any publication. The image
build workflow does not change GHCR visibility; review it explicitly in the registry.

## Prepare and review

From the repository root, create a draft outside the working tree:

```sh
uv run --locked python -m deploy.tinfoil.prepare_release \
  --output /tmp/aiab-enclave-public-candidate \
  --image ghcr.io/lucid-computing-labs/aiab-introspection-enclave
```

The command never accesses ambient credentials, publishes files, or copies the
source repository. It refuses an existing output path. Without `--digest`, the
image digest is all zeros and the measurement workflow refuses release. The
candidate policy digest is an offline output for review, not an approval decision.

The private source repository has a manual-only `.github/workflows/build-image.yml`
(also retained in `workflows/build-image.yml` here). Once image publication and
visibility are approved, run it on the reviewed source commit with the approved GHCR
image name. It builds `deploy/Dockerfile` with pinned Buildx/BuildKit and fixed
amd64 exporter settings, publishes the image, and records its registry digest in
the job summary. Compare the registry digest to independent clean reproducibility
builds using `deploy/reproducible-build.sh`; retain the source commit and evidence.
The workflow does not publish the private Git repository.

Regenerate a fresh candidate with `--digest sha256:ACTUAL_REGISTRY_DIGEST`.
Review every file, the actual registry visibility, effective policy, and application
source/image evidence. Publish only that candidate to the separately approved
public config repository; install the Tinfoil GitHub App there. Tag the reviewed
config commit, then manually dispatch `measure-release.yml` on the tag. The pinned
official action publishes the measured release and marks it latest (the SDK's
release discovery requires latest). Independently approve its `tinfoil.hash`;
do not use a discovered endpoint's digest as an approval source.

The pinned SDK and CLI discover the latest release. Publishing another latest
release can therefore make the gateway reject an enclave still running the old
release, even before traffic is switched. Coordinate release publication,
deployment verification and gateway pins as a maintenance operation. Rollback to
an earlier enclave also requires restoring compatible latest-release discovery and
its previously approved pins; keeping the old hostname alone is insufficient.
Never bypass verification to mask this expected fail-closed transition.

## Secret delivery and deployment

The candidate requires all three repository secrets: `TINFOIL_API_KEY`,
`ANTHROPIC_API_KEY`, and `AIAB_GATEWAY_TOKEN`. The last must be a fresh random token
of at least 32 URL-safe characters. Keep the matching host copy outside the
repository with mode 0400 and ownership readable only by the gateway's UID 65532;
follow the gateway runbook's host-directory protections. The existing provider secret files remain
`/etc/aiab-introspection/tinfoil.secret` and
`/etc/aiab-introspection/anthropic.secret`. Transfer their bytes directly on stdin
to `tinfoil repo secret create OWNER/REPO NAME --value-file -`; never echo them,
place values in command arguments, or write them into GitHub Actions settings.

Management login uses a separate admin credential. The inference key is not an
admin login. [Standard secrets](https://docs.tinfoil.sh/containers/secrets-and-env-vars)
are accessible to Tinfoil infrastructure during deployment and injected as
environment variables. The Anthropic env key is accepted only in Tinfoil mode;
ordinary hosted Docker retains file-only credentials. Private secrets require a
separate keyserver design and are not implemented here.

After account access, price, visibility, image, release and policy are approved:

```sh
tinfoil container create aiab-introspection --repo OWNER/REPO --tag VERSION \
  --secret TINFOIL_API_KEY --secret ANTHROPIC_API_KEY --secret AIAB_GATEWAY_TOKEN
```

Explicitly select all three secrets: the secrets documentation requires selection,
while the CLI reference describes merging declared existing secrets for some
lifecycle commands. Explicit flags avoid depending on those differing descriptions.
Do not enable debug mode. Verify the returned hostname against the independently approved
release and policy using `auditor verify` before activating any browser route.

## Verified gateway and acceptance

Follow [the verified gateway runbook](../gateway/README.md). Keep the current
Caddy Basic Auth in place; switch only its AIAB API backend after the gateway has
verified the enclave's release, TLS identity and policy. The gateway injects the
private token over attested TLS. Direct `/v1/*` calls without it must return 401
before reading a request body, classifier invocation, upstream spending or admission.
Public audit and health routes remain available for independent auditors.

The host terminates browser HTTPS and sees plaintext. The enclave subsequently
sends governed requests to Anthropic over ordinary HTTPS; Anthropic sees them.
This migration attests AIAB's execution and verifies the gateway-to-AIAB TLS hop;
it does not make either the host or Anthropic confidential-computing boundaries.

Acceptance must demonstrate: unauthenticated browser requests remain 401; direct
enclave inference without the token is 401; benign authorized inference succeeds;
capabilities input is blocked; signed reports account for both admitted requests;
gateway wrong release/policy pins refuse requests; host/SDK verification failures
fail closed. Keep the old loopback service available for rollback until these pass.
