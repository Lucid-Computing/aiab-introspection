# ui/ — the agreement UI

Where the lab and the auditor edit, dry-run, sign and track the plan, and watch the build and attestation. Design: section 4 of [docs/AIAB_DESIGN_SPEC.md](../docs/AIAB_DESIGN_SPEC.md). Nothing here is part of the enclave image.

## Run it locally

```sh
cd ui
uv sync                                   # creates ui/.venv; installs ../static editable
make account NAME=teddy PARTY=lab         # prompts for a password; writes ui/accounts.json (git-ignored)
make account NAME=caleb PARTY=auditor
make dev-keys                             # dev keys for lab/auditor/logger in ~/.aiab-dev + trust.json
AIAB_TRUST_FILE=$HOME/.aiab-dev/trust.json make dev
# fish shell:  env AIAB_TRUST_FILE=$HOME/.aiab-dev/trust.json make dev
```

Open http://127.0.0.1:8090 and log in. The UI reads and edits the checkout's `../plan` folder directly; commit with git as usual. Without `AIAB_TRUST_FILE` the UI pins the keys in `static/aiab/baked/trust.json`, which is right for reading the committed plan but means you cannot sign (those private keys are not yours). Optional: `AIAB_BOX_URL=https://…` makes the Attest screen read the box's `/aiab/manifest`.

To try the signing flow: on the Sign screen copy the `sign` command, run it in a terminal from `static/`, upload the `.sig` file it writes.

## How to read this (in order)

The UI calls the static part's own functions, so it cannot disagree with the box:

| File | What it is |
| --- | --- |
| [`../static/aiab/plan_api.py`](../static/aiab/plan_api.py) | The contract a plan is written against: five types and `run_llm`. |
| [`../static/aiab/baked/bundle.py`](../static/aiab/baked/bundle.py) | `bundle_files`, `file_digest` (the plan digest), `load_bundle` (the boot-time checks), `restrictions`. |
| [`../static/aiab/baked/worker.py`](../static/aiab/baked/worker.py) | The sandboxed process that runs `plan.py`'s six functions in order. |
| [`../static/auditor/baked.py`](../static/auditor/baked.py) | `digest`, `sign`, `verify`, `commit-evaluation` and the run/report client. |
| [`../plan/`](../plan/) | The agreement: `plan.py`, `plan.json`, prompts, tests, signatures. |
| [`server/config.py`](server/config.py) | Environment variables → settings. |
| [`server/pipeline.py`](server/pipeline.py) | The graph the UI draws: the six steps and which plan.json fields each takes. |
| [`server/plansource.py`](server/plansource.py) | Read and replace one function in `plan.py`, checked with the box's `check_source`. |
| [`server/auth.py`](server/auth.py) | Local accounts with hashed passwords, signed session cookie, `python -m server.auth add`. |
| [`server/plan_view.py`](server/plan_view.py) | Turns the plan folder into what the screens show; saves settings, functions, prompts, signatures. |
| [`server/box.py`](server/box.py) | Reads the box's manifest and health. Never writes. |
| [`server/app.py`](server/app.py) | The routes, one per screen or form. |
| [`server/templates/`](server/templates/) | One HTML file per screen. No JavaScript. |
| [`server/devkeys.py`](server/devkeys.py) | Development keys in the box's format, and a trust file pinning them. |
| [`assets/`](assets/) | `swiss.css` vendored from lucidcomputing.ai; `aiab.css` adds the few things this UI needs. |

## Agreements and the deploy slot

Each agreement is a folder under `agreements/<name>/` with the same layout as `plan/`. `plan/` is the deploy slot: the one folder the image is built from. "New agreement" copies the deploy slot into a new folder (code, prompt and settings; no signatures, no evaluation). "Deploy" copies an agreement's folder into `plan/`; commit that and the build picks it up. The list shows which agreement is selected for deployment. `AIAB_AGREEMENTS_DIR` moves the folder (default `../agreements`).

## What this first version does and does not do

Does: login per party; the how-it-works screen; agreements as folders with a deploy slot; the plan flow with per-step settings, function body and prompt editing, written straight to the files; the box's own loader check on every load; the plan digest, a standalone signing tool and signature paste verified against the trust file; lock on a valid signature; reading the box manifest and comparing plan digests.

Does not yet: the dry run (deferred; the static part's `python -m aiab.dryrun` works from the terminal), GitHub (branches, pull requests, comments, CI, auto-merge, tags), GitHub login, the attestation verifier, run submission. The state lives in the plan folder and the box; the server keeps only login sessions, no database.

## Deploying

The server is one container (see [`Dockerfile`](Dockerfile), built from the repository root) with the checkout's `plan/` mounted at `/data/plan` and the accounts file at `/data/accounts.json`. On the existing demo host it is one more Caddy route and compose service next to the webchat. On the Lucid tailnet, `tailscale serve` in front of port 8090 gives HTTPS without any DNS work.

Environment: `AIAB_PLAN_DIR` (default `../plan`), `AIAB_TRUST_FILE` (default `static/aiab/baked/trust.json`), `AIAB_BOX_URL` (optional), `UI_ACCOUNTS_FILE` (default `ui/accounts.json`), `UI_SESSION_SECRET` (random per start if unset). No private signing key, deploy credential or box admin credential is ever configured here.

## Integration contract with the box (from master, Jon)

The UI owns drafting, review, preview, offline signature collection and run/report
presentation. It remains outside the enclave image. The active target is section 4
of [v0.5](../docs/AIAB_DESIGN_SPEC.md); see [operations](../docs/BAKED_PLAN_OPERATIONS.md)
for the actual schemas and commands.

The plan editor changes `plan/plan.json`, `plan/plan.py` and prompt files. Both
parties sign the exact folder digest offline using `python -m auditor.baked`;
private keys never go to the UI, CI or box. The PR contains the bundle and both
signature files. The static verifier checks signatures and restrictions, then the
release builds the bundle into the image. Do not call `POST /aiab/plans`: runtime
registration is disabled in the baked service.

Read `GET /aiab/manifest` for restrictions, plan/image commitments, model binding
and output capacity; verify these through the attested client against the approved
release. Submission is `POST /aiab/runs` with complete records, scope and authenticated
source evidence. Poll `GET /aiab/runs/{run_id}/report` and verify the v3 signed
envelope. A display of a server-supplied hash alone is not attestation verification.
