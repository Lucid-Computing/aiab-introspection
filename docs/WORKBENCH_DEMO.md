# Audit workbench demonstration

Environment: https://aiab-dev-astra-one.onrender.com/workbench, branch
`dev-astra-one`. This service has separate storage and accounts. The local operator
login file is `~/.local/share/aiab-dev-astra-one/login.json`; do not commit or share
that file in presentation materials. Use the authenticated workbench, not the
legacy builder without `?workbench=1`.

WorkOS login uses the same Staging OIDC issuer, client and role mapping as dev.
The isolated service has its own callback URL registered on that application:
`https://aiab-dev-astra-one.onrender.com/login/callback`. Its session signing key
and local account storage remain separate. Use **Sign in with WorkOS** for your
existing WorkOS account.

Hosted checks on November 5 passed: a real Pi/DeepSeek investigation completed
four turns and eight tool calls in about 36 seconds; verified authoring changed
only the requested title; a deterministic evaluation matched all 16 selected
synthetic cases. Commit bindings and verification metadata are recorded in
`static/agent_runtime/review/hosted-qualification.json`. These are bounded smoke
checks, not a general accuracy or confidentiality certification.

## A presentation sequence

1. Choose a catalog question about agent coordination, scope violations, or campaign
   efficiency. Start in the readable agreement. Ask the audience to identify the
   question, permitted sources, subjects, investigation tools and result recipients.
   Show that document, graph and code describe the same editable plan.
2. Change a question or threshold. Show the semantic difference, save, and check the
   new version. For assisted editing, request a specific change, inspect the returned
   unsigned suggestion, and explicitly accept the intended change groups. A model
   suggestion never signs, saves or runs an agreement on its own.
3. Rehearse against Northstar. This executes actual tools and methods against
   fictional records. Catalog plans include an investigating DeepSeek agent through
   verified Tinfoil inference. Begin with a small selected investigation and explicit
   budgets; the entire corpus can be expensive. Do not describe a recorded result
   as a live run. Use the displayed status and cancellation controls.
4. Open a finding, supporting references and counterevidence. For a synthetic
   rehearsal, open a cited source record. Discuss coverage and alternate explanations.
   An insufficient-evidence result is distinct from no finding or a failed run.
5. Run a deterministic evaluation of the saved plan with a case cap of 16. Inspect
   the exact evaluated plan and any differences, family metrics, denominators,
   abstentions and citation errors. Live-agent evaluation is separate, capped at
   four cases and incurs real inference. The synthetic development benchmark is
   not independent evidence of accuracy on real organizations.
6. Show exact-version review notes and challenge lineage. For an authorized
   agreement, demonstrate real browser-held approvals and recipient-specific
   releases only after the operator has provisioned independent source authority,
   identities, roster and release policy. A plan edit cannot inherit old approvals.
   Verify a receipt using an independently obtained public key; explain that this
   verifies the signed package, not the correctness of its conclusions.

For employee privacy, show the **separate** controlled collector demonstration:

```sh
uv sync --project static --locked
static/.venv/bin/python static/deploy/workbench/synthetic_demo.py
```

This exercises real signing, encryption, consent, release accounting and receipt
verification with synthetic identities and explicitly simulated attestation. It
does not attest the Render host. Render confidential admission remains blocked.
The production deployment prerequisites are in
[the confidential runtime guide](../static/deploy/workbench/README.md).

## Operations and reproducibility

The isolated service ID is `srv-dbmbeatbedkc73bp6m9g`. Manual deployment is:

```sh
git push origin dev-astra-one
render deploys create srv-dbmbeatbedkc73bp6m9g --confirm -o json
```

Check deployment status before starting a run. A rollout can interrupt running
investigations; interrupted work must not be presented as completed. The branch
workflow needs `RENDER_DEV_ASTRA_ONE_DEPLOY_HOOK` set to this service's hook. Until
that secret is configured, a push alone does not update the served application.

Record the deployed commit, plan digest, corpus seed, model profile, run/evaluation
IDs, observed status and verification artifact for each presentation. The local
qualification artifacts under `static/agent_runtime/review/` are recorded runs,
not evidence that the current hosted run succeeded. Fixture generation and scoring
commands are in [the dataset guide](../static/aiab/workbench/fixtures/README.md).

The environment has no automatic expiry. It continues consuming Render resources
until an operator suspends/deletes this specific service. Preserve only intentionally
retained results before deleting its disk. Do not reset accounts, keys or storage
on the existing dev/prod services. Do not upload real employee evidence to this
synthetic demo to demonstrate a confidentiality guarantee it does not provide.
