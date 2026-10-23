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

To sign: the Sign screen shows one `openssl` command (OpenSSL 3; on a Mac `brew install openssl`) that signs the version digest with your private key and prints the signature; paste it and press Verify and store. Making a key pair is two more `openssl` lines, and the public key is submitted on the same screen: it replaces your party's key in the trust file the UI uses, and when `AIAB_DEPLOY_BASE` is set the UI also opens a pull request (`keys/<party>-…`) that puts it into `static/aiab/baked/trust.json` on that branch for a person to review. `make dev-keys` writes both the box's raw `.key` form and a `.pem` copy for openssl, and can be re-run: existing keys are kept and only missing files are added. Without OpenSSL 3, `assets/sign_digest.py` does the same, and its `--submit` option posts the signature to the UI without pasting.

Deploy pushes: it copies the agreement into `plan/`, makes a branch from the deploy base (`AIAB_DEPLOY_BASE`, normally `production`; default the remote's default branch) with only that change (in a temporary worktree, your checkout is not switched), pushes it, and opens the pull request when the GitHub CLI is logged in (`gh auth login`); otherwise it shows the compare link. You merge the pull request yourself. With `AIAB_PUSH_DRAFTS=1` every save also commits `agreements/` in the checkout and pushes it to the checkout's own branch a few seconds later, so the server disk is never the only copy of a draft. The branch model is in [docs/BRANCHES_AND_RELEASE.md](../docs/BRANCHES_AND_RELEASE.md).

## Logs: what the logging proxy recorded

The Logs screen shows every call that went through the logging proxy, grouped into sessions, one request's full transcript at a time (system prompt, tools and system reminders folded). It needs the proxy's shared key in `AIAB_PROXY_KEY` (locally in `ui/.env`, git-ignored; on Render an environment variable) and `AIAB_PROXY_URL` (default the test proxy). Segments are downloaded into `AIAB_LOGS_DIR` (default `ui/logs/`, git-ignored; on Render `/data/logs` on the disk, so they survive redeploys), checked against the sha256 the proxy lists, and never deleted. A background thread fetches new segments every minute from the moment the server starts; Refresh fetches at once.

## Runs: the deployed plan against the live box

The Runs screen performs the six steps of `static/demo/audit/walkthrough.py` from the browser, each as one call of `python -m auditor.baked` from the static part: verify the plan's signatures, attest the box and pin its identity, sign the chosen records with the demo logger key, submit them, wait for the signed report, verify the report offline. The input is chosen on the page: a test transcript from `plan/tests/transcripts/`, or recorded sessions from the Logs page, which `server/proxylogs.py` turns into box records (`stream` is `proxy`, `agent_id` is the proxy's user name); the page warns when the plan's `input_scope` would make the box refuse them. The page shows what each command returned. It needs `lab.token` and `logger.key` in `AIAB_DEMO_KEYS` (default `~/.local/share/aiab-audit-demo/`), held by the box operator, and the deployed `plan/` must be the one the release pins name, otherwise step 2 refuses. Runs are folders under `AIAB_RUNS_DIR` (default `ui/runs/`, git-ignored) holding the CLI's output files and `state.json`.

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
| [`server/runs.py`](server/runs.py) | The Runs screen's steps: each one a call of the static part's `auditor.baked` CLI, results on disk. |
| [`server/proxylogs.py`](server/proxylogs.py) | The Logs screen: fetches the logging proxy's segments (every minute in the background), groups requests into sessions, converts them to box records. |
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

Does: login per party; the Runs screen that drives the auditor CLI against the live box; the how-it-works screen; agreements as folders with a deploy slot, create, delete; the plan flow with per-step settings, function body (with a code editor) and prompt editing, written straight to the files, and the function's docstring shown as its description; a change log with lab/auditor attribution; the box's own loader check on every load; the plan digest, a standalone signing tool and signature paste verified against the trust file; lock on a valid signature; Deploy that pushes a plan-only branch and opens the pull request; reading the box manifest and comparing plan digests.

Does not yet: the dry run (deferred; the static part's `python -m aiab.dryrun` works from the terminal), merging (people and CI do that), GitHub login, comments, the attestation verifier, run submission. The state lives in the agreement folders and the box; the server keeps only login sessions, no database.

## Plan chat

Set `ANTHROPIC_API_KEY` in the backend environment (on Render, in the service's
Environment settings; locally, in git-ignored `ui/.env`). Plan chat uses
`claude-opus-5-5`. The key stays on the backend; messages and the current plan
bundle are sent to Anthropic, including the plan schema and runtime restrictions.
This authoring model does not change the audit model pinned inside the plan.

Choose **Create with AI** when making an agreement, or use the chat panel on an
unsigned plan. AI creation is disabled when the server has no key. Send a request,
review the summary and expandable file diffs, then **Apply to draft**, **Revise**,
or **Discard proposal**. You can keep chatting to refine an unapplied proposal,
including one that failed structural validation. Each request carries at most one
candidate (34 files, 64 KiB per file, 256 KiB total), with the authoritative draft
digest. Generated files are never executed during chat. The box's bundle loader
checks structure before a proposal can be applied, and checks it again on save.
These checks do not run behavioral tests or establish classifier correctness.
A proposal cannot apply after the draft
has changed or acquired a valid signature. Applying records the actor in Changes,
clears evaluation results for the old version, and uses the existing draft push
workflow. Apply backs up all touched files, evaluation and history before writing,
and rolls them back on a save failure, including partial history writes. Signing
and deployment remain separate actions.

Conversation history and the pending proposal exist only in the open page's
memory. Applying refreshes the saved plan without clearing chat; applied and
discarded actions become explicit conversation context. Navigation and **New
chat** warn before clearing conversation, typed messages or pending proposals.
Ctrl/Cmd+Enter sends; Enter inserts a newline. Failed requests keep the message
available for retry without adding duplicate turns. **Stop waiting** cancels the
browser's wait; the server/provider may still finish and bill the request.
An expired session exposes a sign-in link in a new tab and backs up only the typed
message in sessionStorage for recovery after sign-in. Provider errors are shown
without raw response bodies or credentials.

Run feature checks with `PYTHONPATH=. uv run pytest -q tests` from `ui/`.

The browser regression fixture uses temporary draft files and a mocked Anthropic
transport; it does not call Anthropic or modify production. From the repository
root, run `PYTHONPATH=ui uv run --project ui python ui/tests/browser/plan_chat_server.py`.
In another terminal, run `node ui/tests/browser/plan_chat.cjs` with Playwright on
Node's module path (or set `PLAYWRIGHT_MODULE` to its installed module directory).
Chrome defaults to `/usr/bin/google-chrome`; override with `CHROME_PATH`.
The script covers desktop and mobile interaction, proposal repair and revision,
retry, cancellation, navigation protection, sign-in recovery, and Apply.

## Deploying on Render

The UI is one Docker container with a persistent disk. [`../render.yaml`](../render.yaml) defines it; [`Dockerfile`](Dockerfile) builds it (from the repository root, filtered by `Dockerfile.dockerignore`); [`docker-entrypoint.sh`](docker-entrypoint.sh) runs on every start.

1. Push the branch named in `render.yaml` (`branch:`) to GitHub.
2. On render.com, sign in with GitHub, choose **New → Blueprint**, pick this repository. Render reads `render.yaml` and asks for the values marked `sync: false`:
   - `GITHUB_TOKEN`: a GitHub token that can push branches and open pull requests on this repository (fine-grained token with Contents and Pull requests read/write, or a bot account's token). Deploy pushes with it.
   - `UI_BOOTSTRAP_ACCOUNTS`: the UI logins to create on first boot, `teddy:lab:<password>,caleb:auditor:<password>`. Remove the variable afterwards.
   - `LAB_TOKEN`, `LOGGER_KEY`: optional, only for the Runs screen; from the box operator.
   - `AIAB_PROXY_KEY`: optional, only for the Logs screen; the logging proxy's shared key from 1Password.
3. Apply. Render builds the image and starts it; the first start clones the repository onto the disk at `/data/repo`, creates the accounts, and copies the box's trust file to `/data/trust.json`.
4. Open the service URL, log in, and submit each party's public key on a Sign screen, as locally.

What lives where: the image holds the UI code and the static part it imports; the disk holds the checkout the UI edits (`/data/repo`), the accounts, the trust file and the runs. Redeploys replace the image and keep the disk. `AIAB_CHECKOUT_BRANCH` is the branch whose `agreements/` and `plan/` the UI works on. Agreement drafts live on that disk until deployed; the deploy branch carries only `plan/` because CI accepts plan-only changes.

To try the container locally against a throwaway bare repository instead of GitHub:

```sh
docker build -f ui/Dockerfile -t aiab-ui .
docker run --rm -p 8090:8090 -v /path/to/origin.git:/origin.git -v aiab-data:/data \
  -e AIAB_REPO=Lucid-Computing/aiab-introspection -e AIAB_REPO_URL=file:///origin.git \
  -e AIAB_CHECKOUT_BRANCH=ui-design -e UI_BOOTSTRAP_ACCOUNTS=teddy:lab:lab-pass-123 aiab-ui
```

Environment, for any host: `AIAB_PLAN_DIR` (default `../plan`), `AIAB_AGREEMENTS_DIR` (default `../agreements`), `AIAB_GIT_DIR` (the repository Deploy pushes from, default the checkout), `AIAB_DEPLOY_BASE` (base branch for deploy and key PRs, default `production`), `AIAB_TRUST_FILE` (default `static/aiab/baked/trust.json`), `AIAB_BOX_URL` (default the host in the release pins), `AIAB_RUNS_DIR`, `AIAB_DEMO_KEYS`, `AIAB_RELEASE_FILE`, `AIAB_PUSH_DRAFTS`, `UI_ACCOUNTS_FILE` (default `ui/accounts.json`), `UI_SESSION_SECRET` (random per start if unset). The container's entrypoint sets the path variables from `AIAB_DATA` (default `/data`). No private signing key is ever configured on the server.

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

### Production snapshots and editable drafts

Build the Render service from `master`. On every restart the entrypoint fetches
`AIAB_RELEASE_BRANCH` (default `production`) into a separate Git repository under
`/data/release-source.git` and selects `/data/releases/<commit>`. Runs use that
snapshot's signed `plan/` and release pins. Unsaved changes under `/data/repo`
remain intact and cannot block this update or invalidate the deployed plan's
signatures. Fetch failures stop startup instead of silently using stale pins.
Restart after a successful enclave release to pick up its committed pins.

`AIAB_RUN_PLAN_DIR` can select a deployed plan for local development. It is separate
from `AIAB_PLAN_DIR`, which is the editable draft. When `AIAB_BOX_URL` is unset,
the status view uses the host in `AIAB_RELEASE_FILE`. The status view is informational;
the run still verifies attestation through the Tinfoil SDK against pinned measurements.

### Existing live Render service

`https://aiab-ui-latest.onrender.com` is service `srv-db911ck9v7es73e644o0`
in workspace `tea-db8urpei0phs7393qci0`. This existing service is **image-backed**,
not Git-backed: pushing `master` or changing `render.yaml` does not deploy it.
The blueprint above describes a separate Git-backed setup.

The tested UI code from commit `bde6e99` is published privately as
`ghcr.io/lucid-computing/aiab-ui:bde6e99`, pinned for deployment to
`sha256:58a021525ab2983344e8f880d73efe7535917ff2fa049ce299b6d81daf22c9b0`.
Render uses registry credential `Lucid AIAB UI (derpyplops)` to pull it.

For future UI changes, build `ui/Dockerfile` from the repository root, push a new
commit-tagged image to that repository, then update this service's image digest
with `render services update <service-id> --image <image@sha256:digest>` and run
`render deploys create <service-id> --wait`. Confirm the deployment succeeds,
then test `/runs` through offline report verification. Preserve `/data` and the
existing service environment; do not replace its disk to refresh a deployment.
