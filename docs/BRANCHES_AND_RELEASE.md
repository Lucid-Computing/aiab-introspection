# Branches, agreements and releases

How a change travels from the agreement UI to the running box, and which branch holds what.

## Branches

| Branch | Holds | Changed by |
| --- | --- | --- |
| `master` | development of the box (`static/`), the UI (`ui/`), docs | ordinary commits and pull requests |
| `ui-design` | UI work until it is merged into `master` | the UI developer |
| `production` | what the image is built from: `static/`, `trust.json`, `plan/`, plus the agreement drafts in `agreements/` | three ways, below |

Nothing is deployed from `master`. `production` only moves in three ways:

1. **Agreement drafts** (`agreements/<name>/`): they live on the UI server's disk only and are git-ignored; nothing reaches GitHub until a plan is deployed. Several people edit them in the same UI, and a save made from an older version is refused rather than overwriting a newer one. (Decided 24 Oct 2026; `AIAB_PUSH_DRAFTS=1` still pushes drafts to the checkout's branch for anyone who wants that backup.)
2. **The deployed plan** (`plan/`): Deploy in the UI pushes `plan/<name>-<digest8>` and opens a pull request against `production` (`AIAB_DEPLOY_BASE=production`). CI (`approve-plan.yml`, which only watches pull requests into `production`) checks that only `plan/` changed and that both signatures verify against `production`'s `static/aiab/baked/trust.json`, then merges it. No person clicks.
3. **A party's public key** (`static/aiab/baked/trust.json`): when a party submits a public key on the Sign screen, the UI opens a pull request `keys/<party>-…` against `production` that changes only that file. `approve-keys.yml` checks it is well-formed and that nothing else changed, then merges it. The key reaches the box with the next plan deploy: the box verifies the baked plan's signatures against its trust entry at start-up, so a key and a plan signed with it must arrive together, and `release.yml` refuses to run when `plan/` on production is not signed by the keys in `trust.json`. Decided 24 Oct 2026 by Teddy: no human merge for now; when a real auditor is on board, require the other party's review of that file. The keys cannot ride along in a plan pull request: the plan check refuses anything outside `plan/`, and the keys cannot live inside the plan, since the plan is the thing they validate.
4. **Box code** (`static/`): merged from `master` when a change is confirmed, like cutting a release.

## From a merge on `production` to the live box

```
UI Deploy --PR--> production --merge--> release.yml, started by the merge itself
                                   |  runs static/deploy/tinfoil/audit/release.py --execute:
                                   |    build-image.yml on production (image to ghcr.io, reproduced twice), config repo tagged and measured,
                                   |    Tinfoil told to run the new tag, the live box asked for its identity and health
                                   +--> commits the new release pins and the matching config/trust/manifest back to production [skip ci]
```

The box never fetches anything at run time. The plan, the trust keys and the model binding are inside the image, so the hardware attestation covers them. Clients (the UI's Runs page, `scripts/demo-live`) read the release pins from the repository and refuse a box that does not match.

## What the release job needs, once

Repository secrets:

| Secret | What it is | Who has it |
| --- | --- | --- |
| `TINFOIL_API_KEY` | Lucid's Tinfoil control-plane key (the `api_key` in `~/.tinfoil/config.json`) | the box operator |
| `RELEASE_GH_TOKEN` | a GitHub token that may run workflows and download runs here, and push commits and tags and download releases in `lucid-computing-labs/aiab-signed-audit-enclave` (the config repository, still in the old organisation) | repository admin |
| `AUDIT_LAB_TOKEN` | the lab API token for the live verification step | the box operator |
| `AUDIT_LOGGER_KEY` | the demo logger private key; no longer used by the release, kept for now | the box operator |

A merge into `production` starts the release by itself: a human push triggers it directly, and the check workflows, whose merges GitHub does not count as pushes, start it by dispatch right after merging. The Actions tab button is only for re-runs and dry runs. No GitHub environment or reviewer is used.

Tags are `vMAJOR.MINOR.PATCH`; unless one is given, the job takes the patch after the highest tag the config repository already has (tags are never reused). The same `release.py` works from an operator's laptop as before.

## What the release checks, and what it no longer checks

Before the deploy, the job checks one thing: `plan/` on production is signed by the keys in `static/aiab/baked/trust.json`, because the box verifies exactly that at start-up and would otherwise not come up. The build must come from the production commit being released, and the built plan must match the signed one.

After Tinfoil reports the new tag running, the job asks the box for its attestation and health through the same client the UI uses. That is the proof that the live box is the image just measured. The pins (`release.json` and the config, trust, manifest and policy-digest files) are then committed back, also when that last check failed, so the repository always says what the box runs.

Removed on 25 Oct 2026 at Teddy's decision: the comparison of the live Tinfoil container with the last recorded release, and the comparison of the config repository's head with the recorded config commit and files. Both were guards against the box or the config repository being changed outside this procedure, by the same people who hold the keys to change them. They cannot stop such a person, who can also switch the guards off, and they blocked every release after a run that deployed but did not finish. The parties' real protection is elsewhere: clients verify the box's hardware attestation against the public, measured config, and refuse a box that does not match.

The release `repo` is the exact, case-sensitive GitHub repository identity used by Sigstore verification. `control_plane_repo` is the old organisation alias that Tinfoil still stores; it is kept in `release.json` for reference only.
