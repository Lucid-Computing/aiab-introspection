# Agreement UI and the box: where we are and what is next (20 Oct 2026)

## What I did today

I worked out how the agreement UI and the box connect. Two questions: how does a new plan get from the UI to GitHub, and how does the box start using a signed plan. I also ran your demo and my Runs page against the live box with the shared credentials. Both passed.

## The plan

Branches, one job each:

- `master`: development. Box code, UI code, docs. Nothing is deployed from here.
- `ui-design`: my UI work before it is merged into master.
- `production`: the only branch the box image is built from. Holds `static/`, `trust.json`, `plan/` and the agreement drafts under `agreements/`.
- `plan/<name>-<digest>`: one short-lived branch per deployed plan version, made by the UI, merged by CI.

How a plan goes from the UI to the live box, step by step:

1. Lab and auditor edit an agreement in the UI. Every save is written to `agreements/<name>/` and pushed to `production` a few seconds later. No check, drafts are not baked.
2. Each party signs the plan's digest with their own key on their own computer and pastes the signature into the UI. The UI checks it with the box's own code.
3. Deploy in the UI copies the agreement into `plan/`, pushes a branch `plan/<name>-<digest>`, and opens a pull request into `production`.
4. Your CI check (`approve-plan.yml`) runs on that pull request: only `plan/` may have changed, and both signatures must match the public keys in `production`'s `trust.json`. If so it merges. Nobody clicks.
5. The merge triggers `build-image.yml` on `production`: the box image is built, twice for reproducibility, and pushed to the registry.
6. `release.yml` waits for one approval in the GitHub environment `production`, then runs your `release.py`: tag and measure the config in the config repo, tell Tinfoil to run the new image, verify the attestation, write the new `release.json` back to `production`.
7. The UI's Runs page and your demo read `release.json` from the repo and accept only a box that matches it.

Box code and trust keys follow a different path: change on `master`, then merge `master` into `production` when we want a release.

Why these choices:

- A separate `production` branch, so work on `master` never rebuilds or redeploys the box by accident.
- A pull request for the plan even with no human reviewer, because the pull request is where CI checks the two signatures. CI merging it makes Deploy feel like a direct push while keeping the check.
- Drafts pushed on every save, so the UI server's disk is never the only copy of unsigned work.
- Keys in `trust.json` inside the image, never fetched at run time, so the attestation covers them.

## Done today

- `production` created and merged with `master`; the Render UI now works on it.
- `build-image.yml` also builds from `production` (one line).
- New `release.yml`, described in step 6.
- UI: drafts push, Deploy against `production`, Runs reads `release.json` from the repo.
- Details in `docs/BRANCHES_AND_RELEASE.md`.

## What I need from you

1. Review `build-image.yml` and `release.yml`.
2. Secrets into 1Password, then into GitHub: `TINFOIL_API_KEY`, `RELEASE_GH_TOKEN` (runs workflows here, pushes tags and downloads releases in the config repo), `AUDIT_LAB_TOKEN`, `AUDIT_LOGGER_KEY`. Create the GitHub environment `production` with a required reviewer.
3. Replace the demo lab and auditor public keys in `static/aiab/baked/trust.json` with ours. I will send them.
4. GitHub Actions on the organization is blocked right now ("payments failed or spending limit"). I have asked Caleb. Until then no CI job runs.

## Then: the logger

After the above, the last piece is real logs into the box. Today the demo signs test transcripts with a stand-in logger key.

What exists on Alex's side:

- Push: the proxy sends segments of about 1 MB to receivers tagged `tag:worm`. This becomes the real path once segments are signed.
- Pull: a viewer API returns sessions as unsigned, reformatted JSON. Good for development and datasets, not evidence.

What the box needs in a record: agent, session, time, kind, content, plus logger evidence that the records came from the logger and none were dropped or changed.

What you need from Alex:

- The logger's public key, and exactly which bytes it signs.
- The signed format: where the signature and the chain or checkpoint sit. He says it changes until the end of this week.
- Agent and session identity in the records. Today there is only a user name and a request id.
- How completeness is shown, beyond the sequence numbers in segment names.
- Push or pull as the supported path. Push needs the `tag:worm` permission from Andriy.

Then on the box side: verify the evidence when logs arrive, pull from the proxy inside the enclave so plaintext never sits on a dev host, let a run be defined by a time window and agents instead of uploaded records, and a batching rule, since one segment is about as big as one run allows.
