# Staging environment: plan

Draft of 29 Oct 2026, to iterate on. Goal: a second box, a second agreement UI and a second
public config repo that follow a `staging` branch, so production is touched only when a change
is promoted on purpose. Names below are proposals; change them here before anything is created.

## Decisions to settle first

| # | Question | Proposal | Status |
| --- | --- | --- | --- |
| 1 | Which branch is staging? | a new `staging` branch, merged from `master` automatically on every push to `master` (not `master` itself: an environment branch holds state, see "Why not master") | settled 29 Oct |
| 2 | Which keys does the staging box trust? | the same lab, auditor and logger keys as production: `trust.json` on `staging` stays identical to production's, and whoever holds the key pairs signs in both | settled 29 Oct |
| 3 | Names | config repo `Lucid-Computing/aiab-tinfoil-staging-config` (public, in the same org as the code repo; `lucid-computing-labs` in release.json is that org's old alias), container `aiab-staging`, enclave image `ghcr.io/lucid-computing/aiab-introspection-enclave-staging`, Render service `aiab-ui-staging`, UI image tag `ghcr.io/tddy27/aiab-ui:staging` | settled 29 Oct: repo created |
| 4 | Tinfoil tenancy | same Tinfoil organisation and the same `TINFOIL_API_KEY`; one org runs several containers, but each container needs its own config repo (the SDK reads the repo's latest release) | settled 29 Oct |

## The pieces, production next to staging

| Piece | Production (today) | Staging (new) |
| --- | --- | --- |
| Branch the box is built from | `production` | `staging` |
| Enclave image | `ghcr.io/lucid-computing/aiab-introspection-enclave` | `…-enclave-staging` |
| Public config repo (Tinfoil measures it) | `Lucid-Computing/aiab-signed-audit-enclave` | `Lucid-Computing/aiab-tinfoil-staging-config` |
| Tinfoil container | `aiab-signed-audit`, instance `28c10708-…` | `aiab-staging`, new instance id and host |
| Release pins (`static/deploy/tinfoil/audit/release.json`) | repo, host, digests of production | same file path on the `staging` branch, staging values |
| Trust file (`static/aiab/baked/trust.json`) | real lab, auditor and logger keys | the same file, unchanged (decision 2) |
| GitHub secrets for the release | `TINFOIL_API_KEY`, `RELEASE_GH_TOKEN`, `AUDIT_LAB_TOKEN`, `AUDIT_LOGGER_KEY` | the same four, shared: no GitHub environments; the staging box gets the same lab token value |
| Agreement UI | Render `aiab-ui-latest`, image `ghcr.io/tddy27/aiab-ui:latest` | Render `aiab-ui-staging`, image `…:staging`, env vars pointing at `staging` |
| Logging proxy | Alex's test proxy | the same one |

Nothing new is needed for GHCR (the build pushes with the workflow's own token, only the image
name differs) or for Tinfoil credentials (one org key).

## Why not master

An environment branch carries state that `master` must not: the deployed `plan/`, the trust
file and the release pins. If `master` were staging, promoting `master` into `production` would
carry the staging plan and pins along. With `staging` as its own branch, `master` stays code
only, and promotion is one merge, `master` into `production`. Rule from then on: `master` never
receives a merge from `staging` or `production` (no more fast-forwarding `master` to
`production`). State on an environment branch changes only through its own pull requests
(plan deploys, key changes) and its own release pins commits, so merges from `master` never
conflict with it.

## Steps

### A. One-time setup in the dashboards (Teddy)

1. GitHub: create the public config repo `Lucid-Computing/aiab-tinfoil-staging-config`, default branch `main`
   (release.py pushes to `main` and tags there). Put two files in it, copied from the production
   config repo (the same two are in this repo under `static/deploy/tinfoil/audit/tinfoil-config.yml`
   and `static/deploy/tinfoil/workflows/measure-release.yml`). The image digest in the config is
   replaced at the first release. Install the Tinfoil GitHub App on the new repo (Tinfoil
   dashboard, GitHub integration). If `RELEASE_GH_TOKEN` is a fine-grained token, add the new repo
   to it: contents read/write (commits, tags, releases) and actions read/write (dispatch
   `measure-release.yml`).
2. Tinfoil: the four project secrets on that repo (the dashboard allows this only once a
   release exists): `TINFOIL_API_KEY`, `AIAB_LAB_TOKEN`, `AIAB_AUDITOR_TOKEN`, `AIAB_LOGGER_TOKEN`,
   each with the same value as production's. Same values everywhere, like the keys.
3. GitHub: nothing. The release uses the repository secrets for both branches. Only check that
   `RELEASE_GH_TOKEN` may push to the new config repo (a classic token with the `repo` scope
   from someone with write access there does).
4. Render: new web service from the UI image, 1 GB disk at `/data`, environment:
   `AIAB_REPO=Lucid-Computing/aiab-introspection`, `AIAB_CHECKOUT_BRANCH=staging`,
   `AIAB_DEPLOY_BASE=staging`, `AIAB_RELEASE_BRANCH=staging`, `AIAB_AUTO_PULL=1`,
   `GITHUB_TOKEN`, `UI_SESSION_SECRET`, `UI_BOOTSTRAP_ACCOUNTS`, `LAB_TOKEN` and `LOGGER_KEY`
   from step 2, `AIAB_PROXY_KEY`, `ANTHROPIC_API_KEY` if plan chat is wanted.
5. Keys: nothing to make; staging trusts the same keys (decision 2).

### B. Code changes (one pull request on master)

| File | Change |
| --- | --- |
| `.github/workflows/release.yml` | run on push to `production` and `staging`; check out `github.ref_name`; `--source-ref` and the pins push use the branch name; concurrency group per branch; repository secrets shared | done 29 Oct |
| `.github/workflows/approve-plan.yml`, `approve-keys.yml` | `branches: [production, staging]`; the dispatch already passes the base branch |
| `static/deploy/tinfoil/audit/release.py` | read the instance id and the image name from `release.json` (`instance`, `image` fields) instead of the literals at lines 151 and 195; add `--create NAME` for the first release of a new environment: create the container from the first measured tag and record its instance id and host into `release.json` |
| `ui/server/deploystatus.py` | the two `pr["base"] != "production"` checks use `settings.deploy_base` |
| `ui/server/deployed.py` | default branch from `AIAB_DEPLOY_BASE`, not the literal |
| UI labels | "trusted on production" and similar say the branch name |
| `.github/workflows/promote-staging.yml` (new) | on push to `master`: merge `master` into `staging` and push; fails loudly on conflict |
| `docs/BRANCHES_AND_RELEASE.md` | the branch table gains `staging`; the "master never takes merges back" rule |

### C. First staging release (bootstrap)

1. Create `staging` from the current `production` (`git switch -c staging production`):
   `trust.json` and the signed `plan/` stay as they are. Seed `release.json` with the staging
   repo, container name and image fields (digests and host filled by the first release). Push.
2. Run the release once with `--create aiab-staging`: it builds the staging image,
   measures the config, creates the container, verifies it, commits the pins to `staging`.
3. Deploy the Render service; it clones `staging` to `/data/repo` on first start.
4. Check: Agreements shows "running in the box", Runs shows Ready yes, one run completes.

### D. Daily use after that

- Push to `master`: the promote workflow merges it into `staging`; if `static/` or `plan/`
  changed, a staging release runs (about 4 minutes). UI changes: build and push
  `ghcr.io/tddy27/aiab-ui:staging`, redeploy `aiab-ui-staging`.
- Promote: merge `master` into `production` (a pull request or a push), which releases to the
  production box the same way. Push the same UI image digest as `:latest` and redeploy
  `aiab-ui-latest`.
- Agreements and keys in each environment go through that environment's own pull requests,
  as today.

## What a Tinfoil container is

One confidential VM. It is created from a public config repo at a tagged release that the
repo's `measure-release.yml` workflow has measured (the VM's expected hash, published as a
GitHub release). `tinfoil container create NAME --repo OWNER/REPO --tag vX.Y.Z` boots it; it
gets a hostname `NAME.lucid-computing.containers.tinfoil.dev` and an instance id. Clients
verify the hardware attestation against the hash in the repo's latest release, which is why
one repo describes one container. Updates are `POST /api/containers/<id>/update` with the new
tag, what release.py does for production today.

## Open questions

- Does Tinfoil let one organisation key create a second container by API, or only through
  the CLI? Decides how `--create` in release.py is written.
- Keep Alex's proxy shared between the two environments, or filter sessions by sender?
- Who gets accounts on the staging UI (`UI_BOOTSTRAP_ACCOUNTS`)?
