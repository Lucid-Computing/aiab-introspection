# AIAB implemented design and acceptance contract

Status: implemented signed-plan baseline, reconciled October 15, 2026.

**Full scope is not complete.** The user has since made every originally optional
requirement mandatory. [REQUIREMENTS_COMPLIANCE.md](REQUIREMENTS_COMPLIANCE.md)
records the full requirement set, current gaps and completion criteria. The
following describes the existing baseline, not the full completion contract.

Baseline history: This document replaces
the conflicting [v0.5 proposal](archive/AIAB_DESIGN_SPEC_v0.5.md). The user's later
instructions take precedence: input is text; one plan program produces a value
whose schema is jointly agreed; plans register at runtime; the logger may be
synthetic until its implementation arrives; the UI is owned separately.

The `master` version of this filename is the older v0.5 proposal preserved in
[the archive](archive/AIAB_DESIGN_SPEC_v0.5.md). Master commit `7f4de4a` adds a
six-function plan skeleton based on that proposal; it is preserved under `plan/`
but is not wired into the deployed runtime. See [plan/README.md](../plan/README.md)
for the contract differences.

## 1. Three parts and their interfaces

| Part | Inputs | Outputs |
|---|---|---|
| Static executor (`static/`) | Jointly signed plan; complete text and authenticated source evidence | Accepted plan digest, run ID, bounded signed report, public manifest/identity/health |
| Dynamic agreement (`plan/`) | `run(text, model, policy)` sees complete verified text and restricted capabilities | Result matching the signed `output_schema` |
| Agreement UI (`ui/`, separate owner) | Draft agreements, reviews, evaluation records, signature files, verified deployment/run data | Signed files for registration and a view of verified reports |

The executor never imports uploaded Python. It interprets a small reviewed AST
language. The only external plan capability is one call to the pinned classifier.
There is no separate report program. The static boundary validates, wraps and signs
the returned value. No appeals, per-session filtering, multi-call graphs or arbitrary
Python execution are part of this baseline.

The exact routes, schemas, language and limits are in [AUDIT_SERVICE.md](AUDIT_SERVICE.md).
Inputs are complete at submission, never truncated or appended after admission.
The whole text can contain many prompts and tool interactions. Scope is the signed
source/time-window assertion; it does not infer agent identities from prose.

## 2. Agreement lifecycle

Build and approve a generic executor image first. An agreement pins that image,
model artifact and serving release, trusted parties/source, scope, classification
policy/examples, decision program, output schema, limits, and evaluation record.
Both parties sign the same canonical JSON. Registering a changed agreement requires
two new signatures but no rebuild if the executor already supports its contents.

For evaluation, both parties authorize a candidate plan. `auditor.evaluate` executes
it against labelled synthetic fixtures using the real deployed service and records
counts, errors, denominators, per-case signed reports and timings. `prepare-plan`
checks the decision configuration is unchanged, inserts dataset/evaluation hashes,
and emits an **unsigned** final plan. Each party reviews and signs it separately.
A hash records agreement with the evaluation artifact; it is not proof of general
classifier reliability. Errors are not clean results, and zero denominators are undefined.

## 3. Execution and confidentiality

The CPU audit service runs in a Tinfoil confidential VM. Its separate GPU inference
service is connected over attested TLS. The client verifies the audit release/policy
before sending text; the executor verifies the model serving release, artifact
binding and connection key before sending text to inference. No model stub,
unverified transport fallback, redirect following or inference retry is allowed.

The current CPU host uses AMD SEV-SNP; Intel TDX specifically is not a claim of this
deployment. The agreed architectural requirement is confidential Tinfoil execution,
with CPU and inference trust boundaries both verified. Hardware/provider/verification
code and reviewed workload source remain trust dependencies.

Authorization is enforced within the measured executor. Only the agreed public keys
can authorize plans. Role tokens control admission and retrieval. Run state is bounded
and per boot; restart changes boot identity and report key and loses queued/stored
state. A report must be archived before restart if historical access is required.
Dropping RAM references is not a verified wipe. Raw logs must not enter CI artifacts.

The synthetic exporter uses real signatures and exact-byte chaining. Native logger
checkpoint and completeness verification is excluded until its details are provided.
Neither hardware attestation nor these synthetic signatures prove comprehensive logging.

## 4. Reports and offline verification

Reports have a fixed signed envelope and a plan-defined result schema. The current
wire contract is `aiab-report/v2`; implementation must not silently add fields under
that version. Report checks bind boot, policy, plan, input and run and validate the
result. The signed plan transitively pins the executor image and model artifacts.

The finite schema permits computation of a conservative result-payload capacity.
`aiab-plan schema-info` and offline verification expose `egress_max_bits`, derived
from the signed schema and byte cap. It is verifier-derived information, not an
extra v2 envelope field. It excludes metadata, errors, timing and repeated runs.

The auditor may pin a report identity through an attested live connection, archive
a bundle, and later verify signatures/bindings/schema without a network connection.
The separately trusted identity anchor is mandatory; a bundle cannot supply its own
trust root. A new recipient needs that anchor through an independently trusted channel.
This workflow does not claim portable, independently hardware-verifiable endorsement
of the application report key. Tinfoil's V3 attested-key feature is a possible future
way to supply that stronger proof; it is not enabled in the current V2 deployment.
See [operator tooling](AUDIT_OPERATIONS.md) for exact commands and trust assumptions.

## 5. Release automation

GitHub-hosted Actions test source, build twice, compare OCI image digests, inspect
contents and publish build evidence. The public config repository runs Tinfoil's
measurement workflow. No CI runner is installed inside the enclave.

A local release coordinator uses existing operator credentials to dispatch those
workflows, carry verified digests between stages, publish the measured config,
update the expected Tinfoil instance, verify pinned identity/readiness and run
synthetic acceptance. Its default mode is planning only. `--execute` authorizes the
whole selected release, including deployment. It writes a stage journal and marks
success only after acceptance. It does not merge arbitrary PRs or hold party signing
keys in CI. Concurrent or failed operations require state inspection before retry;
there is no silent rollback to a different unapproved release.

## 6. Acceptance and exclusions

Baseline acceptance requires:

1. Valid dual signatures; altered/unknown-party plans rejected.
2. Authenticated text evidence checked before model execution.
3. Exact reviewed model serving path; no truncation or fail-open behavior.
4. Program output constrained by the agreed schema, with explicit signed failures.
5. Independent report verification and offline re-verification against a saved trusted identity.
6. Reproducible build, measured release and tested automated deployment.
7. Reusable evaluation with exact decision/dataset/result commitments.
8. Bounded execution and explicit restart/capacity/timeout behavior.
9. Publicly documented live location, access requirements, reproducible commands and evidence.

The [acceptance evidence](../static/demo/audit/evidence/README.md) distinguishes local
negative tests from real Tinfoil runs. Tests do not constitute independent security review.

Separate workstreams: UI plan maker and native logger integration. Previously deferred extensions:
full graph execution, multiple models, appeals, transparency publication, persistent
execution ledger, sealed storage, verified wipe, durable replay/disclosure budgets,
continuous monitoring and alerting on missing reports. These were excluded from
the original baseline. Items explicitly listed in the
full requirements (including multiple models, appeals, filtering, agent tools and
verified wipe) are now required for overall completion; see the compliance matrix.
Source access must be granted to reviewing parties;
the current source repository and runtime image remain private.
