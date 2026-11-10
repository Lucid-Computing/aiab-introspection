# AIAB (Auditor-in-a-Box)

Privacy-preserving inference introspection inside a confidential VM. The lab and the auditor agree on an audit plan, both sign it, and the box runs exactly that plan over signed logs and returns a small signed report.

**Full requirements are not yet complete.** See the [requirement-by-requirement audit and remaining work](docs/REQUIREMENTS_COMPLIANCE.md).

The current implementation targets the folder-based v0.5 spec. See [baked-plan operations](docs/BAKED_PLAN_OPERATIONS.md) for signing, build/deployment, submission and report verification. [AUDIT_SERVICE.md](docs/AUDIT_SERVICE.md) documents the prior registered-JSON API.

Live audit API: https://aiab-signed-audit.lucid-computing.containers.tinfoil.dev ([health](https://aiab-signed-audit.lucid-computing.containers.tinfoil.dev/aiab/health)). See [deployment setup](static/deploy/tinfoil/audit/README.md) and the [current run commands](docs/BAKED_PLAN_OPERATIONS.md).

Explore a recorded run: [Inside an audit](https://aiab-signed-audit-run.surge.sh). Operator commands: [evaluation, offline verification and release automation](docs/AUDIT_OPERATIONS.md).

The repository is split into four parts. See [docs/AIAB_DESIGN_SPEC.md](docs/AIAB_DESIGN_SPEC.md) for the design.

| Folder | Part | Owner | Changes |
| --- | --- | --- | --- |
| [`static/`](static/) | The box: runtime, auditor tooling, deployment, tests. Python project root. | Jonathan | With development |
| [`plan/`](plan/) | An example audit plan: code, prompts, settings, both parties' signatures. The CLI commands in the docs use it. Agreements the UI edits live under `agreements/` (git-ignored) and travel to the box signed, with each run; nothing is baked into the image. | Jonathan | With every agreement |
| [`ui/`](ui/) | The agreement UI: edit, review, sign and run agreements; show attestation and runs. Never part of the image. | Jonathan | Independently |
| [`logger/`](logger/) | The logger's side: a server that runs next to the logging proxy, gives the UI log metadata and seals the chosen records to the box. Its own image; never part of the box image. | Teddy | Independently |

Shared material lives at the root: `docs/` (specs), `.github/` (workflows), `.dockerignore` (the image build allowlist; the build context is this directory).

Quick start for the box:

```sh
cd static
uv sync --locked
make check          # lint + tests
make image          # builds the enclave image from the repository root
```

The box's own README is at [static/README.md](static/README.md).
