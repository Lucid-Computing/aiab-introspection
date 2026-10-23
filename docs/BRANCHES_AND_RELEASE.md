# Branches, agreements and releases

How a change travels from the agreement UI to the running box, and which branch holds what.

## Branches

| Branch | Holds | Changed by |
| --- | --- | --- |
| `master` | development of the box (`static/`), the UI (`ui/`), docs | ordinary commits and pull requests |
| `ui-design` | UI work until it is merged into `master` | the UI developer |
| `production` | what the image is built from: `static/`, `trust.json`, `plan/`, plus the agreement drafts in `agreements/` | three ways, below |

Nothing is deployed from `master`. `production` only moves in three ways:

1. **Agreement drafts** (`agreements/<name>/`): the UI commits and pushes them directly after each save (`AIAB_PUSH_DRAFTS=1`). Drafts are not baked and not checked by CI, so a plain push is right. The UI's checkout is `production` (`AIAB_CHECKOUT_BRANCH=production`).
2. **The deployed plan** (`plan/`): Deploy in the UI pushes `plan/<name>-<digest8>` and opens a pull request against `production` (`AIAB_DEPLOY_BASE=production`). CI (`approve-plan.yml`, which only watches pull requests into `production`) checks that only `plan/` changed and that both signatures verify against `production`'s `static/aiab/baked/trust.json`, then merges it. No person clicks.
3. **A party's public key** (`static/aiab/baked/trust.json`): when a party submits a public key on the Sign screen, the UI opens a pull request `keys/<party>-…` against `production` that changes only that file. `approve-keys.yml` checks it is well-formed and that nothing else changed; it never merges. A person merges, because changing who the parties are is the one decision the automation must not make. The keys cannot ride along in a plan pull request: the plan check refuses anything outside `plan/`, and the keys cannot live inside the plan, since the plan is the thing they validate.
4. **Box code** (`static/`): merged from `master` when a change is confirmed, like cutting a release.

## From a merge on `production` to the live box

```
UI Deploy --PR--> production --merge--> release.yml, started by the merge itself
                                   |  runs static/deploy/tinfoil/audit/release.py --execute:
                                   |    build-image.yml on production (image to ghcr.io, reproduced twice), config repo tagged and measured,
                                   |    Tinfoil told to run the new tag, live identity verified, synthetic acceptance
                                   +--> commits the new release pins (release.json) back to production [skip ci]
```

The box never fetches anything at run time. The plan, the trust keys and the model binding are inside the image, so the hardware attestation covers them. Clients (the UI's Runs page, `scripts/demo-live`) read the release pins from the repository and refuse a box that does not match.

## What the release job needs, once

Repository secrets:

| Secret | What it is | Who has it |
| --- | --- | --- |
| `TINFOIL_API_KEY` | Lucid's Tinfoil control-plane key (the `api_key` in `~/.tinfoil/config.json`) | the box operator |
| `RELEASE_GH_TOKEN` | a GitHub token that may run workflows and download runs here, and push commits and tags and download releases in `lucid-computing-labs/aiab-signed-audit-enclave` (the config repository, still in the old organisation) | repository admin |
| `AUDIT_LAB_TOKEN` | the lab API token for the live verification step | the box operator |
| `AUDIT_LOGGER_KEY` | the demo logger private key for the synthetic acceptance run | the box operator |

A merge into `production` starts the release by itself; the Actions tab button is only for re-runs and dry runs. No GitHub environment or reviewer is used.

Tags are `vMAJOR.MINOR.PATCH`; the job bumps the patch of the current tag unless one is given. The same `release.py` works from an operator's laptop as before.
