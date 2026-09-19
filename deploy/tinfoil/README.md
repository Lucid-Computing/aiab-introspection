# Tinfoil release and deployment procedure

For the Anthropic + authenticated gateway deployment, use the concrete
[migration runbook](MIGRATION.md) and `prepare_release.py`. Its separate public
candidate and pinned workflows supersede the generic OpenAI-origin template below.
No candidate has been published or deployed. Never send direct unauthenticated
traffic-generator requests to the new token-protected inference endpoint; use the
authenticated host route after verified gateway acceptance.

This is a reviewed configuration template and an offline-tested auditor integration,
not a deployed service. No public hostname, approved image digest, release digest,
or approved policy digest exists yet. Do not treat the all-zero image placeholder
as a release. The current baseline is Python SDK 0.14.0, CLI
[v0.18.5](https://github.com/tinfoilsh/tinfoil-cli/releases/tag/v0.18.5), and CVM
0.14.9 as requested for the v0.1.1 measured release, reviewed 2026-09-19.

## Prepare a release

1. Choose a public GitHub `owner/repo` with the Tinfoil GitHub App installed and a
   Tinfoil organization with Containers enabled. Choose the registry/image name,
   public HTTPS governed-inference origin, instance name, and release tag.
2. Copy `deploy/tinfoil/tinfoil-config.example.yml` to **repository-root
   `tinfoil-config.yml`**, then replace every `REPLACE_*` value. This root filename
   is required; leaving the template under `deploy/` does not configure a deployment.
   Review all non-secret settings and the classifier definition with the auditor.
3. Use the two release workflows from the official
   [public-container template](https://github.com/tinfoilsh/tinfoil-public-containers-template/tree/main/.github/workflows).
   Review and pin workflow actions; configure the build step with
   `file: deploy/Dockerfile`, `platforms: linux/amd64`, `SOURCE_DATE_EPOCH=0`, and
   fixed exporter/provenance settings. This repository intentionally does not
   install a workflow that can publish an unconfigured release.
4. The first workflow builds/pushes the image, substitutes its actual **registry
   digest** into the root config on a tagged release commit, and dispatches the
   measurement/publish workflow on that tag. The default branch can keep the
   placeholder; the tagged release must contain the real digest. For a prebuilt
   image, put its verified registry digest into the config before measuring it.
   Wait for both phases and independently review the tag's exact config, source,
   image reference, and signed build evidence. See
   [building images](https://docs.tinfoil.sh/containers/building-images) and the
   [config reference](https://docs.tinfoil.sh/containers/configuration).

The application image is non-root (`65532:65532`), with an explicit read-only root
filesystem, no persistent volumes, no extra capabilities, and a writable `/tmp`
tmpfs. SDK public caches use `XDG_CACHE_HOME=/tmp/aiab-cache` and
`XDG_DATA_HOME=/tmp/aiab-data`; the SDK's cache-scoping secret is supplied from fresh
in-memory randomness. Request bodies, responses, signing keys and reports are not
persisted. Do not mount policy files over the bundled image. See
[runtime controls](https://docs.tinfoil.sh/containers/config-runtime).

## Runtime, secrets and egress

`AIAB_MODE=tinfoil` requires the real classifier key, verifies its SDK connection at
startup, and serves **plain HTTP on internal port 8080**. The enclave shim provides
the externally attested TLS boundary and reaches this listener over its private
bridge. The template makes `LISTEN_PORT` and `shim.upstream-port` both 8080 and
exposes only the supported chat/model/audit paths. Local mode retains self-signed
TLS on port 8443. This routing follows the docs; it still needs a live deployment
acceptance check. See [network routing](https://docs.tinfoil.sh/containers/config-networking)
and [verified connections](https://docs.tinfoil.sh/containers/connecting).

If classifier verification fails at startup, Tinfoil mode aborts the application
lifespan, cleans up clients/workers, and the ASGI server exits unsuccessfully. Local
mode retains its diagnostic 503 health behavior. The template runs
`python -m aiab.healthcheck`, a loopback `/aiab/health` probe using Python's urllib
with environment proxies disabled. It requires HTTP 200 and `status=ready`, so a
running process alone cannot establish readiness. Runtime classifier-attestation
failure still closes admission and makes health return 503.

Create `TINFOIL_API_KEY` as a **Tinfoil repository or organization secret**, with an
inference credential (`tk_...`). The template refers only to its name. Never put the
value into GitHub, the image, the public JSON settings, or the measured YAML.
Management CLI login uses a separate admin credential. `tinfoil login` prompts for
the admin key; its saved login can be overridden by the shell's `TINFOIL_API_KEY`,
so run management commands in a shell without the inference key exported. Add the
inference secret using the dashboard, or securely stream it on stdin:

```sh
tinfoil repo secret create OWNER/REPO TINFOIL_API_KEY --value-file -
```

This command expects secret bytes on stdin; use a secret manager or a non-echoing
input mechanism. Do not type a literal key into shell command arguments.
See [CLI secrets](https://docs.tinfoil.sh/containers/cli#secrets).

The template deliberately uses `egress: open`, which Tinfoil documents as public
addresses only; private, link-local and loopback ranges are blocked. SDK router
discovery and verification need `atc.tinfoil.sh`, selected router hostnames,
`github-proxy.tinfoil.sh`, platform certificate proxies, Sigstore/TUF hosts and
possible redirects. A complete stable hostname allowlist has not been established.
The governed origin must therefore be reachable over the public network; the
laptop's Compose-only `http://inference:8080` will not work here. Use HTTPS for this
external governed-inference hop.

Application destinations remain constrained: user requests cannot choose an upstream
URL, the proxy uses the frozen inference origin without redirects/environment
proxies, and classifier calls use only the reviewed SDK's verified router path.
The deployment does **not** claim an externally enforced destination allowlist or a
bandwidth-limited output channel. Establishing and testing a narrower allowlist is
an open question, not grounds to invent a partial allowlist that breaks verification.

## Independently approve the policy and release

Three values have different meanings:

| Value | Meaning and approval source |
|---|---|
| OCI registry image digest (`sha256:...`) | Exact published application image referenced by the tagged config. Review against reproducible build evidence. |
| Tinfoil release digest (64 hex digits) | `tinfoil.hash`/Sigstore attestation subject of the measured release, which commits to CVM/config/image inputs. This is CLI JSON `digest` and SDK `release_digest`. Pass this value to `--expected-digest` / `EXPECTED_AIAB_DIGEST`. It is neither the OCI digest nor a raw SNP/TDX register. |
| Policy digest (64 hex digits) | SHA-256 of the canonical effective policy manifest, independently generated from the reviewed source and approved public settings. Pass it to `--expected-policy-digest` / `EXPECTED_POLICY_DIGEST`. |

Copy `policy-settings.example.json` to a release-specific public settings file and
replace its inference origin and any approved policy values to match the final
config. From the **reviewed release source** and locked dependencies, run:

```sh
uv run --locked python -m auditor.policy --settings PATH/approved-settings.json \
  > PATH/approved-manifest.json
```

The command prints the digest on stderr, makes no network request, ignores ambient
settings/API keys, and rejects secret or unknown fields. Independently review the
emitted manifest, definition/system-prompt hashes, requested model, enforcement and
sampling settings, timeouts, retention and inference origin. Canonical JSON is the
content without the trailing output newline; hashing the entire newline-terminated
file with `sha256sum` gives a different value. Pin the printed digest only after
review. Repeat whenever approved source or policy changes.

Independently obtain the expected release digest from the approved measured release
and its signed build/measurement evidence; do not copy the digest discovered from
the running endpoint and call that approval. Publish the reviewed manifest and all
three distinctly labeled digests in the release record/README after they exist.
The auditor rejects a newer repository release if its digest has not been approved.
The current upstream verification path selects the repository's latest release;
auditing an older non-latest tag is not supported by this adapter.

The requested classifier model alias is recorded, while SDK verification identifies
the provider's router release. It does not establish an independently pinned
immutable model-weight digest or classifier correctness.

## Deploy and verify

Once the release and secrets exist, the operator can use these current CLI commands
(replace the uppercase values; these commands were not executed during development):

```sh
tinfoil container create aiab-demo --repo OWNER/REPO --tag VERSION --secret TINFOIL_API_KEY
tinfoil container get aiab-demo
```

Use the public container hostname returned by Tinfoil. A custom domain is optional;
register/verify it first and add `--custom-domain HOSTNAME` when creating the
container. Do not enable debug mode for an accepted deployment. See the
[container CLI](https://docs.tinfoil.sh/containers/cli).

Install the reviewed CLI binary from the release above, check its published archive
checksum, and put `tinfoil` on the auditor's PATH. Run:

```sh
tinfoil attestation verify -e HOSTNAME -r OWNER/REPO -j
uv run --locked auditor verify --host HOSTNAME --repo OWNER/REPO \
  --expected-digest APPROVED_TINFOIL_RELEASE_DIGEST \
  --expected-policy-digest APPROVED_POLICY_DIGEST
uv run --locked python -m traffic.generate --url https://HOSTNAME --count 200
# Wait for the relevant 10-second periods plus the 30-second finalization allowance.
uv run --locked auditor rollup --host HOSTNAME --repo OWNER/REPO \
  --expected-digest APPROVED_TINFOIL_RELEASE_DIGEST \
  --expected-policy-digest APPROVED_POLICY_DIGEST --state-file ./aiab-checkpoint.json
```

`report` accepts the same verification flags as `rollup`. The auditor executes
`tinfoil attestation verify -e HOSTNAME -r OWNER/REPO -j` without a shell and with a
60-second command timeout. It requires exit success, JSON status `ok`, exact repo/
host/digest, complete recognized measurements and matching attestation/TLS keys.
It rejects `enclave_only`, failed/incomplete/unknown JSON and debug evidence; the
reviewed CLI verifier enforces non-debug hardware policies (its JSON has no debug
boolean). Schema changes require explicit adapter review.

After that, all artifact requests use Python `SecureClient(transport="tls")`'s
verified HTTP client. The adapter checks the successful verification steps,
release, repo, host, code measurement and TLS key against the CLI record before
and after each request. SDK key rotation or a release change requires a fresh
audit. Redirects are disabled; only fixed audit paths are requested. SDK-produced
TLS transport, not an ordinary unverified HTTP client, links the signing key to the
attested application. Request timeout is 15 seconds; SDK verification performs
additional network operations and has no single end-to-end initialization deadline
in this adapter.

Direct CLI diagnostics can use
`tinfoil http get https://HOSTNAME/aiab/health -e HOSTNAME -r OWNER/REPO`.
That command checks provider verification but does not replace this auditor's
independent release/policy approval checks. Ordinary browser chat and the traffic
generator use Web PKI HTTPS, not this auditor's attested transport; use only the
synthetic demo corpus with them.

The report key still rotates on reboot, retained history can disappear, and a
checkpoint cannot prove complete coverage or detect unseen forks. Verify → send a
benign prompt → send the capabilities preset → wait → rollup remains an endpoint
demonstration, with the claim boundaries in [THREAT_MODEL.md](../../docs/THREAT_MODEL.md).

## Validation record and outstanding release inputs

Local tests mock the CLI and SDK; they do not demonstrate live hardware attestation.
`make image` succeeds, and `make image-reproducible` produced matching clean OCI
manifest digests on 2026-09-17:
`sha256:ca38ef042321b8507c790fc2d4002e594afa6fb4fd1e058dca4e7577985e1039`.
This used checksum-verified Buildx 0.37.1, pinned BuildKit 0.26.2, two `--no-cache`
amd64 builds and timestamp rewriting. Both OCI exports were inspected across every
layer and contain no Python bytecode or application cache directories. The
[reproducibility record](../../docs/REPRODUCIBILITY.md) contains the exact method,
config digest, retained evidence and limits. This local result is **not** an
approved registry/release digest; repeat and independently review the exact release.

Still needed: the organization and public repository, reviewed release workflows,
registry/image name and actual published digest, release tag and independently
approved Tinfoil release digest, public HTTPS inference origin, inference secret,
public enclave hostname (or configured custom domain), approved public policy
settings/manifest/digest, and live classification/deployment/attestation acceptance
results. There has been no image publication or public deployment.
