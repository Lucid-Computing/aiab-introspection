# ui/

The agreement UI: where the lab and the auditor edit, review and sign the plan, watch the build and attestation, and submit runs and read reports. See section 4 of [docs/AIAB_DESIGN_SPEC.md](../docs/AIAB_DESIGN_SPEC.md).

It talks to GitHub (plan PRs and signature files) and to the box's read-only endpoints (`/aiab/manifest`, `/aiab/pubkey`, `/aiab/health`, run reports). Nothing here is part of the enclave image: the root `.dockerignore` is an allowlist that only admits reviewed runtime sources under `static/`.

The box's own chat-demo front end lives with the box at `static/webchat/`.

The signed-plan baseline uses the CLI, with no agreement UI required. The future UI must submit jointly signed JSON plans through the [implemented API](../docs/AUDIT_SERVICE.md).
