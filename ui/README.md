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

Open http://127.0.0.1:8090 and log in. The UI reads and edits agreement folders in the checkout directly. Without `AIAB_TRUST_FILE` the UI pins the keys in `static/aiab/baked/trust.json`, which is right for reading the committed plan but means you cannot sign (those private keys are not yours). Optional: `AIAB_BOX_URL=https://…` shows what the box is running (`/aiab/manifest`).

To sign: the Sign screen shows one `openssl` command (OpenSSL 3; on a Mac `brew install openssl`) that signs the version digest with your private key and prints the signature; paste it and press Verify and store. Making a key pair is two more `openssl` lines, and the public key is submitted on the same screen (it replaces your party's key in the trust file the UI uses). `make dev-keys` writes both the box's raw `.key` form and a `.pem` copy for openssl, and can be re-run: existing keys are kept and only missing files are added. Without OpenSSL 3, `assets/sign_digest.py` does the same, and its `--submit` option posts the signature to the UI without pasting.

Deploy pushes: it copies the agreement into `plan/`, makes a branch from the remote's default branch with only that change (in a temporary worktree, your checkout is not switched), pushes it, and opens the pull request when the GitHub CLI is logged in (`gh auth login`); otherwise it shows the compare link. You merge the pull request yourself. `AIAB_DEPLOY_BASE` overrides the base branch, for example `ui-design` while `master` lacks the new layout.

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
| [`server/publish.py`](server/publish.py) | Deploy's git side: branch from the base, replace `plan/`, commit, push, open the PR with `gh`. |
| [`server/app.py`](server/app.py) | The routes, one per screen or form. |
| [`server/templates/`](server/templates/) | One HTML file per screen. The only scripts: the confirm dialog, and CodeMirror (from cdnjs) for `plan.py` with Tab indentation and syntax colours. |
| [`server/template/`](server/template/) | The minimal valid agreement that "New agreement" starts from. |
| [`server/devkeys.py`](server/devkeys.py) | Development keys in the box's format, and a trust file pinning them. |
| [`assets/sign_digest.py`](assets/sign_digest.py) | Standalone: sign a digest with a private key, or recompute a digest from `plan.zip`. |
| [`tools/proxy_to_records.py`](tools/proxy_to_records.py) | Convert a test-proxy log segment into box records (plan transcripts). |
| [`assets/`](assets/) | `swiss.css` vendored from lucidcomputing.ai; `aiab.css` adds the few things this UI needs. |

## Agreements and the deploy slot

Each agreement is a folder under `agreements/<name>/` with the same layout as `plan/`, plus `history.jsonl`, a log of who changed what (outside the digest). `plan/` is the deploy slot: the one folder the image is built from. "New agreement" creates a folder from the minimal template or as a copy of the deploy slot (no signatures, no evaluation). "Deploy" copies the folder into `plan/` and pushes that change as a pull request. "Delete" removes a folder, never the one selected for deployment. The list shows which agreement is selected. `AIAB_AGREEMENTS_DIR` moves the folder (default `../agreements`).

## What this first version does and does not do

Does: login per party; the how-it-works screen; agreements as folders with a deploy slot, create, delete; the plan flow with per-step settings, function body (with a code editor) and prompt editing, written straight to the files, and the function's docstring shown as its description; a change log with lab/auditor attribution; the box's own loader check on every load; the plan digest, a standalone signing tool and signature paste verified against the trust file; lock on a valid signature; Deploy that pushes a plan-only branch and opens the pull request; reading the box manifest and comparing plan digests.

Does not yet: the dry run (deferred; the static part's `python -m aiab.dryrun` works from the terminal), merging (people and CI do that), GitHub login, comments, the attestation verifier, run submission. The state lives in the agreement folders and the box; the server keeps only login sessions, no database.

## Deploying

The server is one container (see [`Dockerfile`](Dockerfile), built from the repository root) with the checkout's `plan/` mounted at `/data/plan` and the accounts file at `/data/accounts.json`. On the existing demo host it is one more Caddy route and compose service next to the webchat. On the Lucid tailnet, `tailscale serve` in front of port 8090 gives HTTPS without any DNS work.

Environment: `AIAB_PLAN_DIR` (default `../plan`), `AIAB_AGREEMENTS_DIR` (default `../agreements`), `AIAB_GIT_DIR` (the repository Deploy pushes from, default the checkout), `AIAB_DEPLOY_BASE` (base branch for deploy PRs, default the remote's default branch), `AIAB_TRUST_FILE` (default `static/aiab/baked/trust.json`), `AIAB_BOX_URL` (optional), `UI_ACCOUNTS_FILE` (default `ui/accounts.json`), `UI_SESSION_SECRET` (random per start if unset). Deploy uses the git and GitHub CLI credentials of whoever runs the UI. No private signing key or box admin credential is ever configured here.

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
