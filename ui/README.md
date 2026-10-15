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
| [`../static/aiab/plan_api.py`](../static/aiab/plan_api.py) | The contract: the types a plan uses and `run_llm`. The pipeline table the UI draws (`PIPELINE`) is not on master; define it inside `ui/` or agree it with Jon. | ~90 |
| `aiab.baked.bundle`, `auditor.baked` (master) | Plan digest (`AIAB/folder/v1`), restrictions, Ed25519 sign and verify against `static/aiab/baked/trust.json`. **The UI still imports the older `aiab.plan_bundle`; re-pointing is the next task.** | |
| [`../static/aiab/dryrun.py`](../static/aiab/dryrun.py) | Jon's dry runner: `python -m aiab.dryrun ../plan --mock --output FILE`; the evaluation is committed with `auditor.baked commit-evaluation`. The UI's `--record` call must change accordingly. | |
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
