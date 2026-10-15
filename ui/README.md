# ui/ — the agreement UI

Where the lab and the auditor edit, dry-run, sign and track the plan, and watch the build and attestation. Design: section 4 of [docs/AIAB_DESIGN_SPEC.md](../docs/AIAB_DESIGN_SPEC.md). Nothing here is part of the enclave image.

## Run it locally

```sh
cd ui
uv sync                                   # creates ui/.venv; installs ../static (aiab) editable
make account NAME=teddy PARTY=lab         # prompts for a password; writes ui/accounts.json (git-ignored)
make account NAME=caleb PARTY=auditor
make dev-keys                             # two dev party keys in ~/.aiab-dev + trusted_parties.json
AIAB_TRUSTED_PARTIES=$HOME/.aiab-dev/trusted_parties.json make dev
# fish shell:  env AIAB_TRUSTED_PARTIES=$HOME/.aiab-dev/trusted_parties.json make dev
```

Open http://127.0.0.1:8090 and log in. The UI reads and edits the checkout's `../plan` folder directly; commit with git as usual. Optional: `AIAB_BOX_URL=https://…` makes the Attest screen read the box's `/aiab/manifest`.

To try the signing flow: on the Sign screen copy the `sign` command, run it in a terminal, upload the `.sig` file it writes.

## How to read this (in order)

| File | What it is | Lines |
| --- | --- | --- |
| [`../static/aiab/plan_api.py`](../static/aiab/plan_api.py) | The contract: the types a plan uses, the pipeline (step order, kinds, settings), and `run_llm`. | ~130 |
| [`../static/aiab/plan_bundle.py`](../static/aiab/plan_bundle.py) | Plan digest, validation against `restrictions.json`, party keys, Ed25519 sign and verify, and the `python -m aiab.plan_bundle` commands. Used by the box, CI and this UI. | ~400 |
| [`../static/aiab/dryrun.py`](../static/aiab/dryrun.py) | Runs a plan over `plan/tests/` in the spec's call order with the mock classifier; prints FP/FN; `--record` writes the evaluation into the plan. | ~200 |
| [`../plan/`](../plan/) | The sample agreement: `plan.py`, `plan.json`, one prompt, three test transcripts. | |
| [`server/config.py`](server/config.py) | Environment variables → settings. | ~60 |
| [`server/auth.py`](server/auth.py) | Local accounts with hashed passwords, signed session cookie, `python -m server.auth add`. | ~170 |
| [`server/plan_view.py`](server/plan_view.py) | Turns the plan folder into what the screens show; saves settings, function bodies, prompts, signatures; runs the dry run in a subprocess. | ~300 |
| [`server/box.py`](server/box.py) | Reads the box's manifest and health. Never writes. | ~45 |
| [`server/app.py`](server/app.py) | The routes, one per screen or form. | ~250 |
| [`server/templates/`](server/templates/) | One HTML file per screen. No JavaScript. | |
| [`assets/`](assets/) | `swiss.css` vendored from lucidcomputing.ai; `aiab.css` adds the few things this UI needs. | |

## What this first version does and does not do

Does: login per party; the how-it-works screen; the plan graph with per-step settings, function body and prompt editing; validation against the restrictions on every load; dry run with the mock classifier and recording; the plan digest with the commands that reproduce it; signature upload with verification against pinned keys; freeze (first signature) and unfreeze; reading the box manifest and comparing plan digests.

Does not yet: GitHub (branches, pull requests, comments, CI, auto-merge, tags), GitHub login, the real classifier in dry runs, the attestation verifier, run submission. The state lives in the plan folder and the box; the server keeps only login sessions, no database.

## Deploying

The server is one container (see [`Dockerfile`](Dockerfile), built from the repository root) with the checkout's `plan/` mounted at `/data/plan` and the accounts file at `/data/accounts.json`. On the existing demo host it is one more Caddy route and compose service next to the webchat. On the Lucid tailnet, `tailscale serve` in front of port 8090 gives HTTPS without any DNS work.

Environment: `AIAB_PLAN_DIR` (default `../plan`), `AIAB_TRUSTED_PARTIES` (default: the placeholder file in `static/aiab/`), `AIAB_BOX_URL` (optional), `UI_ACCOUNTS_FILE` (default `ui/accounts.json`), `UI_SESSION_SECRET` (random per start if unset), `UI_DRY_RUN_TIMEOUT` (seconds, default 120). No private signing key, deploy credential or box admin credential is ever configured here.
