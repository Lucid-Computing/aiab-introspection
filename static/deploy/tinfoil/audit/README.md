# Dev audit service

The current release (tag, image, plan, release and policy digests) is in
[release.json](release.json). A push to `dev` that changes `static/` releases a new
tag automatically (`.github/workflows/release.yml`), and that workflow commits the
new pins here. The measured config, trust and manifest alongside match that tag in
the public [config repository](https://github.com/Lucid-Computing/aiab-tinfoil-staging-config).

- Endpoint: https://aiab-dev.lucid-computing.containers.tinfoil.dev
- Health: https://aiab-dev.lucid-computing.containers.tinfoil.dev/aiab/health
- Instance: `2f4ec6c2-ba3a-4ba4-bd46-bd1afe8a7ee1`, name `aiab-dev`.
- Resources: two CPU cores, 8 GiB VM memory, no local GPU. CPU host uses AMD SEV-SNP.
- Model: `gpt-oss-safeguard-120b` through Tinfoil's attested router (see below).
- Measured application: `python -m aiab.baked.main`; no SSH/debug interface.
  Root filesystem read-only, tmpfs scratch. Plan subprocesses have a syscall
  allowlist, resource limits and no credentials or service sockets.
- The original chat/proxy container remains separate.

Run submission and report retrieval require role tokens over a verified connection.
On the development machine, test-role keys/tokens are protected files in
`/home/jon/.local/share/aiab-audit-demo-dev/`. Private signing keys stay outside the VM.
Only public keys enter measured trust. Provider credentials remain in the operator's
existing Tinfoil credential store and the configured enclave secrets.

See [full operations and commands](../../../../docs/BAKED_PLAN_OPERATIONS.md) and
[the v0.5 design](../../../../docs/AIAB_DESIGN_SPEC.md).
`release.py` automates build, measurement, supported Tinfoil update, independent
verification and synthetic acceptance once invoked. The supported update is
`POST /api/containers/:id/update` with `tag` and `hold:false`; CLI `relaunch` was
not supported by the existing provider route. Tags are immutable and each measured
config includes its release tag to prevent cross-tag provenance ambiguity.

Restart changes the boot/report key and loses in-memory runs/reports. Archive
reports and a trusted identity anchor first. Every run sends its plan, with both
signatures, inside the submission; the image holds no plan and the box stores none. Native logger completeness,
verified memory wipe, UI, agents and appeals are not claimed by this deployment.

## CVM security updates

**Model access (since v0.0.13, 2026-11-09).** Tinfoil retired the model host the box
pinned (`gpt-oss-safeguard-120b-inf12…`, model release v0.0.10). Its replacement
(`…-inf7…`) refuses outside connections, and it serves model release v0.0.11 while
the model repository's GitHub "latest" release is still v0.0.10, so it cannot be
attested directly. From 2026-11-06 to 2026-11-09 the box therefore failed at start-up
and returned HTTP 502.

The box now attests Tinfoil's router, `inference.tinfoil.sh`
(`tinfoilsh/confidential-model-router`), at its current Sigstore-verified release.
No router digest is pinned, because the router releases every few days. When the
router's TLS key changes, the box attests it again once and resends. The router
verifies the model enclave; the box does not. The box checks that every answer names
an enclave of `gpt-oss-safeguard-120b` (`tinfoil-enclave` header), and
`/aiab/health` reports the attested router release and the enclaves that answered
(`model_route`). The model binding in `static/aiab/audit/model.py` is release v0.0.11,
which loads the same modelwrap root as v0.0.10. The router has no `/tokenize`, so the
context check is a byte-based upper bound that refuses a prompt rather than truncating
it. See PR #49.

Dev was updated on 2026-11-06 from CVM 0.14.9 to **0.14.17**, retaining the
application image and trust policy. This release includes the
[TCP vulnerability patch](https://github.com/tinfoilsh/cvmimage/commit/1dd522d3c09f30585d0a577b9989309e4199c565)
and the [certificate-bundle/toolchain refresh](https://github.com/tinfoilsh/cvmimage/commit/eaa503d742d1613c9fb6bbc19895d781e164f8a6).
The operator CLI is **0.19.19**; `tinfoil update` checks and installs its latest
stable version. Older CLIs without that command need a verified release install first.

For subsequent Tinfoil security notices, update `cvm-version` in the environment's
public config repository (the release script reads its config from there), assign
a new immutable release tag including `AIAB_RELEASE_TAG`, and run
`measure-release.yml` on that tag. Update the dev instance with
`tinfoil container update aiab-dev --tag VERSION --hold=false --yes`, then verify
attestation, application identity and readiness against the new release measurement.
Record the measured config and `release.json` pins on `dev` after verification.
Updating the CLI alone does not update a running CVM. Preserve required reports and
identity anchors before deployment as described above.
