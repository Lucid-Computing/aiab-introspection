# How the UI, GitHub and the box fit together, and what is next

## The picture

**The UI** is where the lab and the auditor draft an agreement: the plan code, its settings, the prompt. Its own code is under `ui/`; the agreements it writes are the folders under `agreements/` at the repository root, and the one chosen for deployment is copied to `plan/`. The UI works on a clone of this repository that lives on the Render disk. Signing happens on each party's own machine with their own private key; the party pastes the resulting signature into the UI, which verifies it with the box's own functions (`static/aiab/audit/protocol.py`). The UI holds no private keys.

**GitHub** is the handover between UI and box. The UI pushes agreement drafts to `production` after each save. On Deploy it opens a pull request that replaces `plan/` with the chosen agreement; CI checks that only `plan/` changed and that both signatures match the public keys in `static/aiab/baked/trust.json`, then merges it by itself.

**The box** is a Docker image built by CI from `production`, with `plan/`, `trust.json` and the model binding inside, running in Tinfoil's confidential VM. A release job runs Jon's `release.py`: build, tell Tinfoil to run the new tag, verify the live attestation, record the new pins. The box never fetches anything at run time; what is inside the image is what the hardware attests.

**An audit run**: a client (the UI's Runs page or `scripts/demo-live`) verifies the box's attestation against the pins, submits signed logs, and receives a report signed by the box. Today the logger is a stand-in; real logs wait on Alex's signed format.

## Branches

| Branch | Meaning |
| --- | --- |
| `master` | development: box code, UI code, docs. Nothing deploys from here |
| `ui-design` | UI work before it is merged into `master` |
| `production` | the only branch the image is built from: `static/`, `trust.json`, `plan/`, agreement drafts. Changed by the UI (drafts, plan pull requests) and by merging confirmed work from `master` |
| `plan/<name>-<digest>` | one branch per deployed agreement version, opened by the UI, merged by CI |

## Why this way

- **A separate `production` branch**, because `master` will keep receiving feature work and every push to it would otherwise rebuild and redeploy the box. Promotion from `master` to `production` is a deliberate step, like cutting a release.
- **Plans still go through a pull request**, even though no person reviews it, because the pull request is where CI verifies the two signatures and the plan-only rule. CI merging it is what makes Deploy feel like a direct push while keeping the check.
- **Drafts are pushed, not only stored on the disk**, because the disk is otherwise the only copy of unsigned work, and drafts cannot ride along in the plan pull request without breaking the plan-only rule.
- **Keys live in `trust.json` inside the image, not in the plan and not fetched at run time**, because the plan is the thing being signed, and anything fetched at run time would escape the attestation.
- **The UI imports the box's own code** for digests, loading and signature checks, so the UI can never accept something the box would refuse.

## Next steps

Box side (Jon):

1. Review the two workflow changes: `build-image.yml` also building from `production`, and the new `release.yml` that runs `release.py` from CI behind an approval.
2. Put the four secrets in Lucid's 1Password and then in GitHub: `TINFOIL_API_KEY`, `RELEASE_GH_TOKEN`, `AUDIT_LAB_TOKEN`, `AUDIT_LOGGER_KEY`. Create a GitHub environment `production` with a required reviewer.
3. Replace the demo lab and auditor public keys in `static/aiab/baked/trust.json` with the real parties' keys (Teddy will send them), merge to `production`, release.
4. Logger line: signed segment format with Alex, the box verifying and pulling real logs, scope-based submission, batching within the box limits.

UI side (Teddy):

5. Point the Render service at `production` (`AIAB_CHECKOUT_BRANCH`, `AIAB_DEPLOY_BASE`, `AIAB_PUSH_DRAFTS=1`).
6. Public-key submission in the UI opens the `trust.json` pull request instead of a hand edit.
7. Then the auditing track: dataset, task, metric, dry run.

## Logger integration: inputs and what to get from Alex

Where logs can come from today, all from Alex's logging proxy:

| Input | Form | Good for |
| --- | --- | --- |
| Pushed segments | `llmproxy-test-<log>-<first>-<last>.jsonl.zst`, about 1 MB, PUT to receivers tagged `tag:worm` on the tailnet; Jon's receiver is `static/aiab/proxy_ingest.py` | the real evidence path, once segments are signed |
| Pull through the viewer API | `/_ui/api/sessions`, `/_ui/api/requests/<id>`, shared password; Alex's `export_transcripts.py` saves sessions as `.jsonl` | development and datasets; unsigned and reformatted, not evidence |
| Generating traffic | `PROXY_KEYS` in 1Password: send your own Claude calls through the proxy so they get logged | test data |

What the box requires before a real log can be admitted (`docs/AIAB_DESIGN_SPEC.md` 2.3): records with `agent_id`, `session_id`, `ts`, `kind`, `content`, plus `logger_evidence` that proves the logger produced them and nothing was dropped or altered.

What Jon needs from Alex, in order:

1. **The logger's public key** and the exact bytes it signs: per record, per segment, or a chained head. This replaces the demo `logger_key` in `trust.json`.
2. **The signed format**: where the signature and the chain or checkpoint live in a segment, and how a gap or a replay would show. Alex said the format changes until the end of the week of 20 Oct.
3. **Identity fields**: today a record has `who` (a user) and a request id, not agent and session. Either the proxy adds them or Alex documents how to derive them; Jon's decoder refuses to guess.
4. **Completeness**: the `first`/`last` sequence numbers in segment names, and whether anything states that no segment is missing for a time window.
5. **Delivery**: the `tag:worm` permission from Andriy so pushed segments reach the receiver, or confirmation that pulling is the supported path.

On the box side the pieces are then: verify the evidence at ingress, pull directly from the proxy inside the enclave so plaintext never sits on a dev host, submission by scope (window and agents) instead of uploaded records, and a batching rule, since a segment is about a megabyte and a run takes at most that much.

Details: `docs/BRANCHES_AND_RELEASE.md`, `ui/README.md`, `context/receiving-test-logs.md` (Alex's note, not in git).
