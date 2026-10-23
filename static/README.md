# Signed-plan audit runtime

This is the Python project for the current auditor-in-a-box. It executes a jointly
signed `plan/` folder inside the measured image and uses attested inference.

```sh
uv sync --locked
make check
uv run python -m aiab.baked.check ../plan
```

`aiab` and `aiab-audit` start `aiab.baked.main` (configured trust and role secrets
are required). `auditor --folder ../plan --help` exposes folder verification,
signing, submission and report verification. The image starts this same baked
runtime by default; it no longer starts an Anthropic proxy.

- [Demo for a new user](../docs/PM_DEMO.md): `../scripts/demo-live`.
- [Plan and run operations](../docs/BAKED_PLAN_OPERATIONS.md).
- [Production release process](../docs/BRANCHES_AND_RELEASE.md).
- [Signed audit deployment](deploy/tinfoil/audit/README.md).
- [Full requirement status](../docs/REQUIREMENTS_COMPLIANCE.md).

The legacy inline proxy/chat experiment, its gateway, traffic generator, deployment
scripts and tests have been removed. Its source remains in Git history. This cleanup
does not stop or delete remote containers or their configuration repositories.
The GHCR image package is still named `aiab-introspection-enclave`: it is also used
by the current audit build, so that shared package name must not be removed.

`aiab.audit` and the registered-plan CLI/evidence are retained because the baked
runtime shares their protocol, model transport, signatures and API machinery.
They are signed-audit history, not the removed inline-proxy experiment.
