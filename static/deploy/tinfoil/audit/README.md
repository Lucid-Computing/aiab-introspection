> The v0.5 migration uses baked folder plans. See [current operations](../../../../docs/BAKED_PLAN_OPERATIONS.md) and `release.json` for current live pins. Historical v0.1.x notes below do not enable runtime registration on the baked service.

# Signed audit release

These files record the public, measured v0.1.3 configuration in
https://github.com/lucid-computing-labs/aiab-signed-audit-enclave/tree/v0.1.3

`release.json` identifies the hostname, repository and expected release/policy pins.
`trust.json` contains public keys and model/runtime identities only. `manifest.json`
and `policy-digest.txt` are independently calculated from that trust configuration.
`tinfoil-config.yml` pins the published image and exposes the audit API, including
`/aiab/runs/*` for reports. The existing chat deployment is separate.

The container is `28c10708-7f8b-4981-8280-dca928a2654b`, 2 CPU / 8 GiB / zero GPUs,
CC enabled, debug disabled. Inference runs at the separately attested model endpoint.
Managed secrets are selected by name; their values are not in these files. Keep
credentials outside Git. Demo role private keys/tokens are stored locally under
`/home/jon/.local/share/aiab-audit-demo/` with private permissions.

The automated coordinator and recovery procedure are documented in [AUDIT_OPERATIONS.md](../../../../docs/AUDIT_OPERATIONS.md). It runs locally and dispatches build/measurement to GitHub Actions; the default is read-only planning.

To reproduce a release: build the application from the private source repo's
manual `build-image.yml`, independently rebuild and compare its OCI digest, update
`trust.runtime_digest`, regenerate the manifest using `Trust(**trust).manifest()`
and its RFC8785 SHA-256 digest, copy the trust JSON and image into the measured
config, commit/tag in the public config repo, then manually dispatch
`measure-release.yml` against that tag. Deploy only after measurement succeeds.
Finally approve the resulting release/policy pins in the client and sign plans
referencing the published image. Do not silently accept a provider's newer release.

As of this deployment, the installed CLI's `container relaunch` invokes an
unavailable `/relaunch` API. The provider's documented `POST /api/containers/:id/update`
with `{"tag":"v0.1.3","hold":false}` worked for the running CPU instance. It preserves
secret bindings. Wait for `current_tag` to match and verify attestation and health.
See https://docs.tinfoil.sh/admin/admin-api for the current lifecycle contract.

The new instance remains running. The original chat instance's scheduled shutdown
configuration does not automatically apply to this separate audit instance.
