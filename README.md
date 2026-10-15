# AIAB (Auditor-in-a-Box)

Privacy-preserving inference introspection inside a confidential VM. The lab and the auditor agree on an audit plan, both sign it, and the box runs exactly that plan over signed logs and returns a small signed report.

The implemented signed-plan API and verification workflow are documented in [docs/AUDIT_SERVICE.md](docs/AUDIT_SERVICE.md).

Live audit API: https://aiab-signed-audit.lucid-computing.containers.tinfoil.dev ([health](https://aiab-signed-audit.lucid-computing.containers.tinfoil.dev/aiab/health)). See [deployment setup](static/deploy/tinfoil/audit/README.md) and the [live E2E command](static/demo/audit/evidence/README.md).

The repository is split into three parts. See [docs/AIAB_DESIGN_SPEC.md](docs/AIAB_DESIGN_SPEC.md) for the design.

| Folder | Part | Owner | Changes |
| --- | --- | --- | --- |
| [`static/`](static/) | The box: runtime, auditor tooling, deployment, tests, and its own chat-demo front end (`static/webchat/`). Python project root. | Jonathan | With development |
| [`plan/`](plan/) | The audit plan: code, prompts, settings, both parties' signatures. Registered as jointly signed data against a pinned executor image. | Teddy | With every agreement |
| [`ui/`](ui/) | The agreement UI: edit, review, sign and track the plan; show attestation and runs. Never part of the image. | Teddy | Independently |

Shared material lives at the root: `docs/` (specs), `.github/` (workflows), `.dockerignore` (the image build allowlist; the build context is this directory).

Quick start for the box:

```sh
cd static
uv sync --locked
make check          # lint + tests
make image          # builds the enclave image from the repository root
```

The old chat-demo README is at [static/README.md](static/README.md).
