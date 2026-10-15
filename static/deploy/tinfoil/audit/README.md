# Live baked-plan audit service

Current release: **v0.2.2**. The exact image, plan, release and policy digests are
in [release.json](release.json). The measured config, trust and manifest alongside
it match that release in the public
[config repository](https://github.com/lucid-computing-labs/aiab-signed-audit-enclave/tree/v0.2.2).

- Endpoint: https://aiab-signed-audit.lucid-computing.containers.tinfoil.dev
- Health: https://aiab-signed-audit.lucid-computing.containers.tinfoil.dev/aiab/health
- Instance: `28c10708-7f8b-4981-8280-dca928a2654b`, name `aiab-signed-audit`.
- Resources: two CPU cores, 8 GiB VM memory, no local GPU; separate attested
  `gpt-oss-safeguard-120b` inference. CPU host uses AMD SEV-SNP.
- Measured application: `python -m aiab.baked.main`; no SSH/debug interface.
  Root filesystem read-only, tmpfs scratch. Plan subprocesses have a syscall
  allowlist, resource limits and no credentials or service sockets.
- The original chat/proxy container remains separate.

Run submission and report retrieval require role tokens over a verified connection.
On the development machine, test-role keys/tokens are protected files in
`/home/jon/.local/share/aiab-audit-demo/`. Private signing keys stay outside the VM.
Only public keys enter measured trust. Provider credentials remain in the operator's
existing Tinfoil credential store and the configured enclave secrets.

See [full operations and commands](../../../../docs/BAKED_PLAN_OPERATIONS.md),
[the v0.5 design](../../../../docs/AIAB_DESIGN_SPEC.md) and
[live evidence](../../../demo/audit/baked-evidence/README.md).
`release.py` automates build, measurement, supported Tinfoil update, independent
verification and synthetic acceptance once invoked. The supported update is
`POST /api/containers/:id/update` with `tag` and `hold:false`; CLI `relaunch` was
not supported by the existing provider route. Tags are immutable and each measured
config includes its release tag to prevent cross-tag provenance ambiguity.

Restart changes the boot/report key and loses in-memory runs/reports. Archive
reports and a trusted identity anchor first. A different plan requires rebuilding
and redeploying; runtime plan registration is disabled. Native logger completeness,
verified memory wipe, UI, agents and appeals are not claimed by this deployment.
