# Independent audit boxes

Each box has its own Tinfoil container, endpoint, signed plan and immutable release
pins. Creating a named box does not update, replace or stop the default box.
The existing service remains `default`, preserving current links and CLI clients.

## Rollout

1. Merge this application's changes into `master`.
2. Merge the release/build/approval workflow and Python changes into `production`,
   retaining production's signed `plan/`, trust keys and release pins.
3. Build and deploy the updated UI image to Render.

The UI refuses a new-box deployment if production still has the legacy approval
workflow: that workflow would otherwise update the default box. No new GitHub or
Render secrets are needed. The Tinfoil account must have sufficient host capacity.

## Create a box for another plan

1. Draft and sign an agreement as usual.
2. On Agreements, open **Deploy to a new box** for that agreement.
3. Enter a unique name such as `dev-audit`, then click **Create box**.
4. The plan-only PR is verified against production's trusted keys and merged.
   The release uses that exact approved source commit even if another PR lands.
5. CI builds and measures the image, creates an additional confidential container,
   and checks its attested identity and readiness. Other boxes keep running.
6. On Runs, choose the new name in **Audit box**, review its release, then select
   logs and start the audit. The submitted form keeps the selected box; if its
   release changed since the page loaded, the user must review it again.

The existing **Deploy** button still updates the default box. An automatic production
push refuses to change the default to a different selected plan: after creating a
named box, use **Deploy** or an explicit release dispatch to choose a default plan.
Editing/removing named catalog entries does not trigger a default release. Named boxes are
immutable deployments in this first version: create a new named box to run a new
version. Release jobs remain serialized because they publish tags in one config
repository. The deployed boxes themselves can run audits independently.

If the agreement already exactly matches production's `plan/`, there is no plan
change for a PR. Create its additional box from GitHub Actions instead:

```sh
gh workflow run release.yml --repo Lucid-Computing/aiab-introspection \
  --ref production -f new_box=dev-audit
```

A dry run checks local release preconditions without creating a box:

```sh
gh workflow run release.yml --repo Lucid-Computing/aiab-introspection \
  --ref production -f new_box=dev-audit -f dry_run=true
```

## What is kept separate

Named release records live under `static/deploy/tinfoil/audit/boxes/<name>.json` on
production. The default keeps its existing `static/deploy/tinfoil/audit/release.json`.
Each record pins `application_source_commit`; the UI obtains the plan and public
trust keys from that immutable commit, not whichever plan is currently at production's
tip. Reports retain the existing plan/image/policy/boot identity verification. A
run also records its box name and endpoint for display.

Creation copies the default container's placement, environment variables and org
secret references. Debug/SSH and non-confidential mode are disabled; custom domains,
replacement IDs and release promotion are not copied. These are separate runtimes,
not separate tenants: they currently share the configured lab/auditor credentials,
logger key and inference account. Changing a key for a future release does not
revoke it in an already-running box; stop that box if its old key must stop working.

## Inspect, stop and remove

A failed creation/deployment remains visible in the release attempt artifact and,
once the provider accepted creation, in the named release record. A catalog entry
alone is not proof of readiness: the audit client still verifies attestation and
health. Do not blindly retry creation after a failure; inspect the recorded
`instance_id` in the attempt first.

```sh
tinfoil container get INSTANCE_ID -o json
tinfoil container stop INSTANCE_ID
# When finished with it permanently:
tinfoil container delete INSTANCE_ID
```

After deletion, remove its `boxes/<name>.json` through a normal reviewed repository
change. Historical audit evidence remains in the UI's runs directory. Each box
uses additional compute; there is no automatic expiry or hidden cleanup.
