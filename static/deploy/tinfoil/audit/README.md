# Dev audit service

Current release: **v0.0.11**. The exact image, plan, release and policy digests are
in [release.json](release.json). The measured config, trust and manifest alongside
it match that release in the public
[config repository](https://github.com/Lucid-Computing/aiab-tinfoil-staging-config/tree/v0.0.11).

- Endpoint: https://aiab-dev.lucid-computing.containers.tinfoil.dev
- Health: https://aiab-dev.lucid-computing.containers.tinfoil.dev/aiab/health
- Instance: `2f4ec6c2-ba3a-4ba4-bd46-bd1afe8a7ee1`, name `aiab-dev`.
- Resources: two CPU cores, 8 GiB VM memory, no local GPU; separate attested
  `gpt-oss-safeguard-120b` inference. CPU host uses AMD SEV-SNP.
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

**Readiness blocked (2026-11-06):** the CVM is running and CLI attestation against
v0.0.11 succeeds, but application endpoints return HTTP 502 after restart. The
baked model dependency `gpt-oss-safeguard-120b-inf12.tinfoil.containers.tinfoil.dev`
no longer resolves. Tinfoil's [inventory](https://atc.tinfoil.sh/inventory) lists
`gpt-oss-safeguard-120b-inf7.tinfoil.containers.tinfoil.dev`; its
[status](https://atc.tinfoil.sh/status) identifies model release v0.0.11, while
GitHub's latest-release endpoint still selects v0.0.10. Direct attestation and
health requests to the replacement host timed out from the development machine.
The original model pin is retained. Recovery requires a reachable, independently
verified model endpoint/release and, if replacing the baked binding, a rebuilt
application and updated trust/policy pins. The CVM update is not a successful
application-readiness verification.

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
