# ui/ — the agreement UI

Where the lab and the auditor edit, dry-run, sign and track the plan, and watch the build and attestation. Design: section 4 of [docs/AIAB_DESIGN_SPEC.md](../docs/AIAB_DESIGN_SPEC.md). Nothing here is part of the enclave image.

## Run it locally

```sh
cd ui
uv sync                                   # creates ui/.venv; installs ../static editable
make account NAME=teddy PARTY=lab         # local login; prompts for a password; writes ui/accounts.json (git-ignored)
make account NAME=caleb PARTY=auditor
make dev-keys                             # dev keys for lab/auditor/logger in ~/.aiab-dev + trust.json
AIAB_TRUST_FILE=$HOME/.aiab-dev/trust.json make dev
# fish shell:  env AIAB_TRUST_FILE=$HOME/.aiab-dev/trust.json make dev
```

Open http://127.0.0.1:8090 and log in. To log in through WorkOS as well as local accounts, see [Login](#login). The UI reads and edits agreement folders in the checkout directly. Without `AIAB_TRUST_FILE` the UI pins the keys in `static/aiab/baked/trust.json`, which is right for reading the committed plan but means you cannot sign (those private keys are not yours). Optional: `AIAB_BOX_URL=https://…` shows what the box is running (`/aiab/manifest`).

For a demo without a lab member at hand, set `AIAB_LAB_KEY` to a lab signing key (base64 raw Ed25519, the content of `lab.key` from `make dev-keys`) and `AIAB_LAB_CERTIFICATE` to the certificate the lab root made for it (the line in `lab.cert`; `make dev` sets both from `~/.aiab-dev` when the files exist). Every page then shows a yellow and black DEV MODE label, and the lab card on the Sign screen shows **Lab Sign** in a yellow and black box to anyone logged in, the auditor included. Pressing it (`POST /a/{id}/sign/lab`, with the digest the page showed) signs that version as the lab with the server's key, records the signature in the change log as made by the server and names who pressed it, and the Sign screen shows it as signed by the server. The route refuses when the plan changed since the page loaded or the box would reject it. Nothing signs by itself: creating, saving, applying from chat and re-opening for editing leave the lab unsigned. The signature freezes the plan like any other. The certificate must have been made by the lab root production trusts, for that key, otherwise there is no Lab Sign, the route refuses and the Sign screen says why. Whoever controls the server, and anyone logged in, can sign as the lab: for the prototype the lab key is treated as not secret.

To make the auditor's signing one click as well, without one shared key, set `AIAB_AUDITOR_ROOT_KEY` to the auditor root's private key (`auditor-root.key` from `make dev-keys`; `make dev` sets it too). Sign on the plan page, the agreements list and the Sign screen then carry `data-certify`: on an auditor's first Sign in a browser, `sign.js` makes a non-extractable Ed25519 key, posts its public half as JSON to `/sign/certificate`, checks that the certificate returned names that key and verifies against the trusted root, keeps the key and certificate in IndexedDB, and signs. Each auditor and each browser thus has a key of its own. The server refuses a root, the lab's key and malformed keys, certifies only for the caller's own party, and appends every certificate it makes to `issued-certificates.jsonl` next to the accounts file (`/data` on Render), with the login's username and display name; for WorkOS logins the username is only the email's local part. It certifies nothing if the root is not the one production trusts, and the Sign screen says why. While it holds the root, the Sign screen offers no browser setup or root form for the auditor and the root route refuses, since either would make a new root. Whoever controls the server can then certify a key and sign as the auditor; [THREAT_MODEL.md](../docs/THREAT_MODEL.md), "Prototype shortcut", lists what the real version changes and what a red-team review found.

To sign by hand: on the Sign screen, Set up signing (`assets/sign.js`) makes the party's root and a signing key with WebCrypto, has the root certify the signing key at once, downloads the root as a key file encrypted under the password the party chose, and keeps only the signing key (non-extractable, in IndexedDB) and its certificate in the browser; the root is not stored. If the box does not trust that root yet, the page submits it through the root form: a pull request (`keys/<party>-…`) that `approve-keys.yml` checks and merges, then the release that builds it into the box, while every page waits. Another browser opens the key file with Use this key file, which certifies a fresh signing key and drops the root again, with no release. A browser set up before root keys offers Continue with this key when the box trusts its key: that key becomes the root and certifies a fresh signing key. Sign this version signs the digest after a confirmation and stores the signature and the certificate through the upload route. Keys are per site, so a key set up on the Render UI is not on a local one. Keys kept outside the browser still work: Other ways to sign shows the `sign_digest.py` lines that make a key, certify it and sign, and the `openssl` command for a PKCS#8 key file; the signature and the certificate are pasted together. The UI never edits the trust file; it verifies certificates against the roots in the checkout's `static/aiab/baked/trust.json`, the same file on the deploy branch that the release bakes into the box. Signatures made with a key the party's root on production did not certify are refused everywhere, including here.

Nothing is deployed for an agreement: a signed agreement is run from the Runs screen, which sends the folder to the box with the records. Agreement drafts stay on the UI server (`agreements/` is git-ignored). Key pull requests use the deploy base (`AIAB_DEPLOY_BASE`, normally `production`) and the GitHub CLI when it is logged in (`gh auth login`). Several people may edit the same agreement: a save made from a page that showed an older version is refused, so nobody overwrites a change they never saw. (`AIAB_PUSH_DRAFTS=1` still exists to push drafts to the checkout's branch, off by default.) The branch model is in [docs/BRANCHES_AND_RELEASE.md](../docs/BRANCHES_AND_RELEASE.md).

## Login

Who may use the UI, and which party they are, is decided outside the agreement: one account per person, each bound to `lab` or `auditor`. Only a user's own party's signature and key submissions are accepted from them.

With `OIDC_ISSUER`, `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` set (in `ui/.env` locally, in the service environment on Render), people sign in through WorkOS AuthKit, or any OpenID Connect provider: *Sign in with WorkOS* sends the browser to the provider (authorization code with PKCE); the provider sends it back to `/login/callback` with a code; the server exchanges the code, checks the ID token's signature against the provider's published keys, its issuer, audience, expiry and nonce, and sets the same signed `aiab_ui` cookie as before (12 hours). Two checks decide who gets in:

- **The provider** says who the person is: their email, checked by WorkOS. An optional provider group (`OIDC_GROUP`, off by default) can also be required in the ID token's groups claim; Okta can put one there (with `OIDC_SCOPES="openid profile email groups"`), WorkOS cannot.
- **The admin list.** [`admins.json`](admins.json) (`UI_ADMINS_FILE` overrides the path) lists the emails that are the lab. Everyone else the provider signs in is the auditor. The file is committed with the code, so the split is decided in the repository: `make admin EMAIL=…` (or `REMOVE=1`), commit, deploy.

The change log records the part of the email before the `@`. Taking a person off the admin list takes effect when their cookie expires. Log out ends the UI session; WorkOS keeps its own, so the next sign-in picks the same person unless you use a private window (Okta, which has an end-session endpoint, gets a full sign-out).

The WorkOS side is one Connect application (OAuth, first-party, confidential, redirect URI `<ui>/login/callback`) with a credential; the issuer is the environment's AuthKit domain. The one for the AIAB › Staging environment exists already; its IDs and the exact clicks are in [docs/OIDC_AUTH_MIGRATION.md](../docs/OIDC_AUTH_MIGRATION.md), which is also the plan for moving a deployment from local accounts.

Local accounts in `UI_ACCOUNTS_FILE` (hashed passwords, `make account`) work next to WorkOS: the login page shows the WorkOS button and the password form. They are for demo users the operator makes on this side, and a way in when WorkOS is down. Without the three settings they are the only login, which is the laptop default.

## Logs: what the logging proxy recorded

The Logs screen shows every call that went through a logging proxy, grouped into sessions, by its metadata only: who, when, model, status, size and message count. The text of a call is never shown or served by this server (the `/logs/earlier` and `/logs/raw` routes answer 404 for proxied calls): it is for the box, which receives it sealed (MAC-377). Sessions are titled "who · when · n calls". There are two proxies, chosen with the selector at the top of the page (`?logs=prod` or `?logs=dev`; Dev Agent Logs is the default), and both can be configured at once. **Prod Agent Logs** needs the reader key in `AIAB_LOG_KEY` (`LOG_READER_KEY` from the `logging-proxy-production-logs` item in 1Password; sent as `x-log-key`; the URL defaults to `https://llm-proxy.lucidcompute.com`, `AIAB_LOG_URL` overrides it). **Dev Agent Logs** needs the test proxy's shared key in `AIAB_PROXY_KEY` (sent as `x-proxy-key`; `AIAB_PROXY_URL` overrides the address). Either goes locally in `ui/.env`, git-ignored, and on Render in an environment variable; a source without a key is shown on the selector but cannot be chosen. Segments are downloaded into a subfolder per source of `AIAB_LOGS_DIR` (`prod/` and `dev/`; default `ui/logs/`, git-ignored; on Render `/data/logs` on the disk, so they survive redeploys), checked against the sha256 the proxy lists, and never deleted. Test segments that an earlier version left at the root of `AIAB_LOGS_DIR` are moved into `dev/` at the first start. A background thread per source fetches new segments every 5 minutes from the moment the server starts (production re-sends its whole live part each time); Refresh fetches the shown source at once. The session list shows 50 sessions per page; a call's earlier messages load only when unfolded. **Slack Logs** is a third source of another kind: a static copy of the public-channel export, one JSON lines file per channel (nothing is fetched or updated by this server; Refresh is off). It is committed to the repository encrypted, as `ui/data/slack-export.enc` (the `*.jsonl` files, tarred, zstd-compressed and sealed with AES-256-GCM; `server/slackexport.py`), because the repository is public and the export is internal. A server that holds the key in `AIAB_SLACK_EXPORT_KEY` (the `slack-export-bundle` item in 1Password) opens the bundle into `AIAB_LOGS_DIR/slack` at every start; on a laptop `AIAB_SLACK_EXPORT` can name the export folder instead and it is read in place. A new scrape is a new bundle: `cd ui && AIAB_SLACK_EXPORT_KEY=... uv run python -m server.slackexport pack <folder>` rewrites the file, which is then committed (each one is about 1 MB with no delta to the last, and stays in the history; `newkey` prints a fresh key for a first bundle). Each message is one record with its author and time; a session is a thread, or a channel and day for messages outside threads, and the page shows a thread whole. **GitHub Logs** is a fourth source, built the same way: the Lucid-Computing organisation's public repositories (forks left out; private repositories are never read), with issues, pull requests, their comments and reviews, and the commits on each default branch (message and changed files, not diffs). `cd ui && uv run python -m server.githubexport fetch <folder>` fetches them through the signed-in `gh` command line, one JSON lines file per repository, and `pack <folder>` seals them into `ui/data/github-export.enc` under `AIAB_GITHUB_EXPORT_KEY` (the `github-export-bundle` item in 1Password); the server opens that bundle into `AIAB_LOGS_DIR/github` at every start, or reads the folder `AIAB_GITHUB_EXPORT` names in place. A session is an issue or pull request thread, or a repository's commits on one day. The sources are listed in `log_sources_from_env` in `server/config.py`; another variety is one more entry there.

## Runs: a signed agreement against the live box

The Runs screen picks an agreement signed by both parties, copies its folder (signatures included) into the run, and performs six steps on that copy, each as one call of `python -m auditor.baked` from the static part: verify the plan's signatures, attest the box and pin its identity, seal the chosen records to the box's encryption key and sign them with the demo logger key (the plain records are then deleted from the run folder; a box that publishes no encryption key gets them unsealed), submit them together with the signed plan, wait for the signed report, verify the report offline against the copy. The input is recorded sessions from one log source of the Logs page (the same selector; a run never mixes sources, and the run's label names the one used), which `server/proxylogs.py` turns into box records (`stream` is `proxy`, `agent_id` is the proxy's user name); the page warns when the plan's `input_scope`, if it has one, would make the box refuse them. The page shows what each command returned. It needs `lab.token` and `logger.key` in `AIAB_DEMO_KEYS` (default `~/.local/share/aiab-audit-demo/`), held by the box operator, and the release pins in the checkout must be the release the box runs, otherwise step 2 refuses. Runs are folders under `AIAB_RUNS_DIR` (default `ui/runs/`, git-ignored) holding the frozen `plan/`, the CLI's output files and `state.json`.

## How to read this (in order)

The UI calls the static part's own functions, so it cannot disagree with the box:

| File | What it is |
| --- | --- |
| [`../static/aiab/baked/plan_ir.py`](../static/aiab/baked/plan_ir.py) | The plan format: the graph's schema, the checks the box applies, the derived report schema and limits. |
| [`../static/aiab/baked/bundle.py`](../static/aiab/baked/bundle.py) | `bundle_files`, `file_digest` (the plan digest), `load_bundle` (the boot-time checks), `restrictions`. |
| [`../static/aiab/baked/graph.py`](../static/aiab/baked/graph.py), [`worker.py`](../static/aiab/baked/worker.py) | The engine that runs the graph, and the sandboxed process it runs in. |
| [`../static/auditor/baked.py`](../static/auditor/baked.py) | `digest`, `sign`, `verify`, `commit-evaluation` and the run/report client. |
| `../agreements/<name>/` | An agreement: `plan.json` (the graph), tests, signatures, `history.jsonl`. Git-ignored. |
| [`server/config.py`](server/config.py) | Environment variables → settings. |
| [`playground/`](playground/) | The plan builder: the graph-and-JSON editor embedded in the plan page, built once into `playground/dist`. |
| [`server/auth.py`](server/auth.py) | Login: OpenID Connect against WorkOS or any provider (authorization code with PKCE, ID token checked, `admins.json` says who is the lab, everyone else is the auditor: `python -m server.auth party`) turned into the UI's signed session cookie; and, next to it or alone, local accounts with hashed passwords (`python -m server.auth add`). |
| [`server/plan_view.py`](server/plan_view.py) | Turns the plan folder into what the screens show; saves the plan through the box's loader, stores signatures, archives old-format agreements. |
| [`server/box.py`](server/box.py) | Reads the box's manifest and health. Never writes. |
| [`server/publish.py`](server/publish.py) | The git side: a key change as a branch and pull request with `gh`, draft backups, the checkout following its branch. |
| [`server/runs.py`](server/runs.py) | The Runs screen's steps: each one a call of the static part's `auditor.baked` CLI, results on disk. |
| [`server/proxylogs.py`](server/proxylogs.py) | The Logs screen: fetches the logging proxy's segments (every 5 minutes in the background), groups requests into sessions, converts them to box records. |
| [`server/feedback.py`](server/feedback.py) | The Feedback form's entries and the post-it notes: one append-only JSONL file each in `AIAB_FEEDBACK_DIR`, and an optional Slack post (`AIAB_FEEDBACK_WEBHOOK`). |
| [`server/app.py`](server/app.py) | The routes, one per screen or form. |
| [`server/templates/`](server/templates/) | One HTML file per screen. The only scripts: the confirm and Feedback dialogs, the chat panel, browser signing (`assets/sign.js`) and post-it notes (`assets/notes.js`); the builder is its own bundle in an iframe. |
| [`server/template/`](server/template/) | The minimal valid agreement that "New agreement" starts from. |
| [`server/devkeys.py`](server/devkeys.py) | Development keys in the box's format: a root and a certified signing key per party, and a trust file pinning the roots. |
| [`assets/sign.js`](assets/sign.js) | Browser signing: makes, backs up, imports and stores the party's key in the browser and signs the digest there; the signed bytes are the protocol's, checked against the Python verifier by `tests/test_browser_signing.py` under Node. |
| [`assets/sign_digest.py`](assets/sign_digest.py) | Standalone: sign a digest with a private key, or recompute a digest from `plan.zip`. |
| [`tools/proxy_to_records.py`](tools/proxy_to_records.py) | Convert a test-proxy log segment into box records (plan transcripts). |
| [`assets/`](assets/) | `swiss.css` vendored from lucidcomputing.ai; `aiab.css` adds the few things this UI needs. |

## The plan builder

`playground/` is the plan builder: a React app that edits the plan as a graph of five block
types (Log Source, Classifier, Run Python, Run LLM, Final Report) or as `plan.json` text, with every
step showing what it reads and what it produces. The plan page embeds it in an iframe at
`/audit-plan-playground/?agreement=<id>`; it loads the agreement's `plan.json` from
`GET /a/<id>/plan.json` and saves with `PUT`, which runs the box's loader
(`aiab.baked.bundle.load_bundle`) and refuses, with the loader's reasons per node, anything
the box would refuse. Saving needs the edit lease and the version the page saw, like every
other save. Without the `agreement` parameter the same page is the standalone playground
(samples, localStorage, no server), also served behind the login.

The builder is built once into `playground/dist` (`pnpm install && pnpm build` in
`playground/`; the Docker image does this in a node stage). Its rules mirror the box's
(`static/aiab/baked/plan_ir.py`): a plan the builder shows as valid is a plan the box loads.
See [playground/README.md](playground/README.md).

Agreements written for the previous plan format (`plan.py` and a `plan.json` of schema
`aiab-baked-plan/v1`) are moved to `agreements/_archive/` when the server starts or the
Agreements page loads. They are listed there read-only with a Delete button; the box no
longer runs that format.

## What this first version does and does not do

Does: login per party; the Runs screen that drives the auditor CLI against the live box; the how-it-works screen; agreements as folders, create, delete; the plan builder (graph and YAML) saving through the box's loader; a change log with lab/auditor attribution; the box's own loader check on every load; the plan digest, a standalone signing tool and signature paste verified against the trust file; lock on a valid signature; Run, which sends the signed agreement with the records; reading the box manifest and health; feedback from any page, as the Feedback form (text and an optional name) or a post-it note left on the page, which the lab reads at `/feedback` (`docs/FEEDBACK_FORM_PLAN.md`).

Does not yet: the dry run (deferred; the static part's `python -m aiab.dryrun` works from the terminal), merging (people and CI do that), GitHub login, comments, the attestation verifier, run submission. The state lives in the agreement folders and the box; the server keeps only login sessions and the feedback files, no database.

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
and running remain separate actions.

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
The Sign screen has its own fixture and script: `ui/tests/browser/sign_server.py` and
`node ui/tests/browser/sign.cjs`, which make a key in Chrome, write it into the fixture's
trust file, sign, forget the key and restore it from the backup file.
In another terminal, run `node ui/tests/browser/plan_chat.cjs` with Playwright on
Node's module path (or set `PLAYWRIGHT_MODULE` to its installed module directory).
Chrome defaults to `/usr/bin/google-chrome`; override with `CHROME_PATH`.
The script covers desktop and mobile interaction, proposal repair and revision,
retry, cancellation, navigation protection, sign-in recovery, and Apply.

## Deploying on Render

The UI is one Docker container with a persistent disk. [`../render.yaml`](../render.yaml) defines it; [`Dockerfile`](Dockerfile) builds it (from the repository root, filtered by `Dockerfile.dockerignore`); [`docker-entrypoint.sh`](docker-entrypoint.sh) runs on every start.

1. Push the branch named in `render.yaml` (`branch:`) to GitHub.
2. On render.com, sign in with GitHub, choose **New → Blueprint**, pick this repository. Render reads `render.yaml` and asks for the values marked `sync: false`:
   - `GITHUB_TOKEN`: a GitHub token that can push branches and open pull requests on this repository (fine-grained token with Contents and Pull requests read/write, or a bot account's token). Key submissions push with it.
   - `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`: the AuthKit domain and the Connect application's client ID and secret; see [Login](#login) and the migration doc for the AIAB › Staging values. Emails in `ui/admins.json` (committed) are the lab; any other WorkOS user is the auditor.
   - `UI_BOOTSTRAP_ACCOUNTS`: optional; local password accounts, `lab@lab.com:lab:<password>,...` (6 characters or more). Checked on every start: names not yet in the accounts file are added, existing ones are left alone. They work next to WorkOS; `make account` in the service's shell adds more.
   - `AIAB_LAB_KEY` and `AIAB_LAB_CERTIFICATE`: optional; a lab signing key and the certificate the lab root made for it, for the demo-only Lab Sign button on the Sign screen.
   - `AIAB_AUDITOR_ROOT_KEY`: optional, prototype; the auditor root's private key, so the server certifies each auditor's browser key on their first Sign.
   - `LAB_TOKEN`, `LOGGER_KEY`: optional, only for the Runs screen; from the box operator.
   - `AIAB_LOG_KEY`: optional, only for the Logs screen; production's log reader key from 1Password.
   - `AIAB_PROXY_KEY`: optional, only for the Logs screen; the test proxy's shared key from 1Password. Both keys can be set: the Logs screen then offers both sources.
   - `AIAB_SLACK_EXPORT_KEY`: optional, only for the Logs screen; the key that opens the committed Slack export bundle (`slack-export-bundle` in 1Password). Slack Logs is then read from the bundle, opened at every start.
   - `AIAB_SLACK_EXPORT`: optional; a folder of the Slack export read in place instead of the bundle (a laptop setting).
   - `AIAB_GITHUB_EXPORT_KEY`: optional, only for the Logs screen; the key that opens the committed GitHub export bundle (`github-export-bundle` in 1Password). `AIAB_GITHUB_EXPORT` names a folder to read in place instead.
3. Apply. Render builds the image and starts it; the first start clones the repository onto the disk at `/data/repo` (and adds the local accounts from `UI_BOOTSTRAP_ACCOUNTS`, if set).
4. Open the service URL, log in, and submit each party's public key on a Sign screen, as locally.

What lives where: the image holds the UI code and the static part it imports; the disk holds the checkout the UI edits (`/data/repo`), the accounts, the trust file and the runs. Redeploys replace the image and keep the disk. `AIAB_CHECKOUT_BRANCH` is the branch whose release pins and trust file the UI works with. Agreement drafts live on that disk and never reach GitHub.

To try the container locally against a throwaway bare repository instead of GitHub:

```sh
docker build --platform linux/amd64 -f ui/Dockerfile -t aiab-ui .   # Render runs amd64 only; a default build on an Apple Silicon Mac is arm64 and will not start there
docker run --rm -p 8090:8090 -v /path/to/origin.git:/origin.git -v aiab-data:/data \
  -e AIAB_REPO=Lucid-Computing/aiab-introspection -e AIAB_REPO_URL=file:///origin.git \
  -e AIAB_CHECKOUT_BRANCH=ui-design -e UI_BOOTSTRAP_ACCOUNTS=teddy:lab:lab-pass-123 aiab-ui
```

Environment, for any host: `AIAB_AGREEMENTS_DIR` (default `../agreements`), `AIAB_GIT_DIR` (the repository key submissions push from, default the checkout), `AIAB_DEPLOY_BASE` (base branch for key PRs, default `production`), `AIAB_TRUST_FILE` (default the checkout's `static/aiab/baked/trust.json`; set it to a dev trust file for local signing with dev keys), `AIAB_BOX_URL` (default the host in the release pins), `AIAB_RUNS_DIR`, `AIAB_DEMO_KEYS`, `AIAB_RELEASE_FILE`, `AIAB_PUSH_DRAFTS`, `AIAB_AUTO_PULL` (fast-forward the checkout to its remote branch every minute when clean; on by default on Render), `OIDC_ISSUER`, `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` (WorkOS or other OpenID Connect login; `OIDC_PROVIDER_NAME`, default `WorkOS`, names it on the login page; `OIDC_GROUP`, off by default; `OIDC_SCOPES`, default `openid profile email`; `UI_ADMINS_FILE`, default `ui/admins.json`; `UI_ACCOUNTS_FILE`, default `ui/accounts.json`, holds local password accounts, which work with or without the provider), `UI_SESSION_SECRET` (random per start if unset). The container's entrypoint sets the path variables from `AIAB_DATA` (default `/data`). No private signing key is ever configured on the server.

## Integration contract with the box (from master, Jon)

The UI owns drafting, review, preview, offline signature collection and run/report
presentation. It remains outside the enclave image. The active target is section 4
of [v0.5](../docs/AIAB_DESIGN_SPEC.md); see [operations](../docs/BAKED_PLAN_OPERATIONS.md)
for the actual schemas and commands.

The plan builder changes an agreement's `plan.json`. Both
parties sign the exact folder digest offline using `python -m auditor.baked`;
private keys never go to the UI, CI or box. Nothing is merged or built for a plan:
each run sends the file and both signatures to the box, which verifies them against
the keys in its image. Do not call `POST /aiab/plans`: it is not served.

Read `GET /aiab/manifest` for restrictions, the image commitment and the model binding;
verify these through the attested client against the approved release. Submission is
`POST /aiab/runs` with complete records, scope, authenticated source evidence and the
signed plan. Poll `GET /aiab/runs/{run_id}/report` and verify the v3 signed
envelope. A display of a server-supplied hash alone is not attestation verification.

### Release pins and editable drafts

Runs verify the box against the release pins in the checkout at `/data/repo`, which
follows its branch (`AIAB_AUTO_PULL`) and is pulled once more when the pins and the box
disagree. Agreement drafts are git-ignored there, so a pull never touches them. When
`AIAB_BOX_URL` is unset, the status view uses the host in `AIAB_RELEASE_FILE`. The status
view is informational; the run still verifies attestation through the Tinfoil SDK against
pinned measurements.

### Existing live Render service

`https://aiab-ui-latest.onrender.com` is service `srv-db911ck9v7es73e644o0`
in workspace `tea-db8urpei0phs7393qci0`. This existing service is **image-backed**,
not Git-backed: `master` reaches it only through the image workflow below, and
changing `render.yaml` does nothing to it.
The blueprint above describes a separate Git-backed setup.

The image is `ghcr.io/lucid-computing/aiab-ui` (private), built by
[`build-ui-image.yml`](../.github/workflows/build-ui-image.yml) on every push to `master`
that touches what `ui/Dockerfile` copies (or by hand: Actions, *Build agreement UI image*,
*Run workflow*). It is tagged with the commit and `latest`, and the workflow then calls
the service's deploy hook (repository secret `RENDER_DEPLOY_HOOK_URL`) with the exact
digest. Render pulls it with registry credential `Lucid AIAB UI (derpyplops)`. After a
deploy, test `/runs` through offline report verification and check `/logs`. Preserve `/data` and the
existing service environment; do not replace its disk to refresh a deployment.

### Diagnosing a run

Execution and verification are separate. A signed failure report can verify successfully
but has **no audit verdict**. The run page and history derive this distinction from the
report itself, including reports saved by older UI versions. Expand the report step for
the signed evidence; use the executor run ID when investigating.

New runtime failures identify a fixed stage (`plan_query_failed`, `plan_metric_failed`,
etc.), a classifier response problem (`model_output_truncated`, `model_output_invalid`),
or a timeout. Error codes never contain prompts, model responses or exception messages.
Older `execution_failed` reports cannot retrospectively reveal their missing stage.
Update the verifier alongside the runtime to recognize the additional error codes.

The Runs page pulls the checkout before admission when the pins and the box disagree, so
a completed release does not require a UI restart. A run keeps its frozen copy of the
agreement throughout all six steps.

The classifier adapter uses low reasoning effort within the unchanged signed token
budget. Default reasoning can exhaust that budget before producing a score; truncation
is a failure, never an all-clear and never silently retried with a larger budget.


### Live dev environment

The dev UI is https://aiab-ui-dev.onrender.com, Render service
`srv-dbi77gvb9u1s73dafbp0`, in the existing project's `dev` environment. It has
its own 1 GB disk at `/data`. The image is `ghcr.io/lucid-computing/aiab-ui:dev`;
`AIAB_CHECKOUT_BRANCH` and `AIAB_DEPLOY_BASE` are both `dev`.
The build workflow uses the repository secret `RENDER_DEPLOY_HOOK_URL_DEV` when
configured. Without that secret, it publishes the image but does not deploy it.

The independent audit instance is `aiab-dev.lucid-computing.containers.tinfoil.dev`
(`2f4ec6c2-ba3a-4ba4-bd46-bd1afe8a7ee1`). It reuses the former staging config repo
`Lucid-Computing/aiab-tinfoil-staging-config` and enclave image name. Its runtime
role tokens are separate from production; CI verifies releases with
`AUDIT_LAB_TOKEN_DEV`. Signing trust keys and provider accounts remain shared.
The dev UI reads the test proxy (Dev Agent Logs); setting `AIAB_LOG_KEY` on it adds Prod Agent Logs beside it.

Dev was bootstrapped from the signed staging baseline. It does not yet include
master's graph-plan migration. Promote code with a review that preserves the
target branch's trust keys and deployment pins. Do not merge
environment state back into master.
