# Full AIAB requirements: completion audit

Audit date: October 15, 2026. Runtime reviewed: release v0.1.3, application commit
`e0ce40cb1738cbdc17de0679447c200a6b3e4d21`. Source reviewed through `28e9364`.

**Overall: incomplete.** The user has explicitly made every item in the supplied
requirements mandatory, including the original “good to have” items. This document
supersedes earlier scope exclusions; it does not change what v0.1.3 implements.
UI and native logger remain separately owned workstreams, but are now dependencies
of full-system acceptance. A synthetic exporter and recorded visualization cannot
satisfy their acceptance requirements.

Status meanings: **Implemented** means code and relevant tests/evidence exist;
**Partial** means only part of the stated requirement is implemented; **Missing**
means it is not implemented. None of these constitutes independent security review.

## Signed plan requirements

| ID | Required behavior | Current status and evidence | Remaining acceptance |
|---|---|---|---|
| P1 | One RSI classification problem with positive and negative examples | **Implemented.** `plan/demo-signed-plan.json`, `protocol.PLAN`, `model.messages`. Full policy and examples enter the classifier's system message. | Preserve this single-classifier profile through extensions. RSI detection quality remains measured, not guaranteed. |
| P2 | Whole input from Merkle Logger within agreed time window | **Partial.** `SignedTextVerifier` verifies exact text bytes, signature, ordered hash chain and equality of the signed time-window scope. `test_logger.py` and full-text/context tests pass. | Native logger proof/checkpoint fixtures and adapter; demonstrate omission, reordering, corruption and wrong-window rejection. The present exporter asserts completeness; it cannot prove the real logger included every event. Oversize input is rejected, never truncated. |
| P3 | One query; agreed prompt, decision rule, output and small-test-set FP/FN | **Implemented for the baseline.** Signed threshold/program/schema and evaluation commitments; `auditor.evaluate`, `prepare-plan`, recorded real inference evaluation and final signed demo. | Preserve one-call baseline. Evaluate each new agent/model/filter/appeal configuration; old metrics do not transfer automatically. Current smoke set: TP=2, TN=1, FP=0, FN=0, errors=0 (FPR 0/1, FNR 0/2). |
| P4 | One classification model pinned by hash | **Implemented for one reviewed model.** Plan pins model artifact root, serving release and endpoint; `model.MODEL_BINDINGS`, `PinnedConnection`, negative transport/model tests. | Keep pinned single-model profile; review each additional model's release-to-weights relationship before admitting it. A model name or advertised hash alone is insufficient. |
| P5 | Defined deterministic reporting and format | **Implemented under the user's later clarification.** One signed plan program creates the result; signed output schema plus deterministic validation/canonicalization/envelope/signature. `program.py`, `report.py`; invalid-output live case. | Do not add a separate report program. Deterministic processing of a model result is not deterministic inference across repeated runs. |
| P6 | No appeal in demo; flagged result final | **Implemented in baseline.** No appeal endpoint; report retrieval never reruns inference. | Preserve explicitly final/no-appeal mode while implementing opt-in, jointly signed appeals in extended plans. |
| P7 | One file jointly signed by lab and auditor; baseline needs no UI | **Implemented mechanically.** `accept_plan` checks both signatures over all plan fields and independently configured keys. CLI signs and assembles one JSON file. | Production parties must hold their own signing keys. Demo identities are locally controlled test keys, not evidence of two independent organizations' approval. |
| P8 | Negotiate different classification problems | **Implemented.** Problem, prompts, examples, threshold and program are signed editable plan fields. | Exercise UI drafting/approval against the same contract; changing a field invalidates old signatures. |
| P9 | Choose information available to classification | **Missing beyond whole-text/time-window scope.** `selection` is fixed to `all`; program cannot select a subset. | Add deterministic signed selection, verify the complete source before filtering, bind selection and selected content to report evidence. Test excluded information never reaches model/tools. Do not infer trusted event timestamps or agent identities from unstructured prose. |
| P10 | Negotiate queries, prompt, metric and output | **Partial.** Prompt, decision program/threshold, examples and finite result schema are negotiable. Single-call classifier and FP/FN evaluation are fixed capabilities. No collaborative comment/review UI exists here. | Extended query/agent modes and UI review workflow; all executable settings signed, evaluation invalidated on semantic changes. |
| P11 | Multiple allowed classifiers; trusted random choice at runtime | **Missing.** Trust and plan admit exactly one model and one inference call. | Signed finite model set and selection/aggregation policy, reviewed artifact bindings for every model, enclave CSPRNG selection without caller-controlled seed or silent fallback, bound selection evidence, real multi-model acceptance. |
| P12 | Negotiate report format | **Implemented for the supported finite JSON Schema subset.** Closed objects, booleans, bounded integers and string enums. Live enum-schema case plus schema-invalid-output rejection. | Preserve finite output constraints for agent/appeal runs; intentionally do not permit free-form explanations or arbitrary JSON Schema references. |
| P13 | Negotiate time window and appeal method | **Partial.** Window is signed and scope-matched. No appeal policy. | Signed appeal window, eligibility, method, prompt/program, maximum attempts and output bounds. Reject unilateral widening. |
| P14 | Agent query with negotiated tools | **Missing.** The current restricted Python interpreter is a classifier expression evaluator, not an agent. | Bounded multi-step agent, allowlisted typed tools, no ambient credentials/network/filesystem capabilities, signed tool arguments/scope and budgets; real confidential agent-model run and adversarial escape tests. |
| P15 | Preview FP/FN before final signing | **Implemented through CLI.** Jointly authorized candidate evaluation, actual reports/counts, `prepare-plan` writes an unsigned final agreement committing to dataset/results. | Integrate with UI. Candidate evaluation authorization and final approval must stay distinct. Display denominators/errors; never present three samples as general accuracy. |
| P16 | Appeal adverse results | **Missing.** No linked appeal state machine, timer or appeal report. | Original signed result remains immutable. Linked appeal limited to an eligible adverse result, jointly authorized secondary query or minimal authenticated correction, deadline and attempt limit; restart-safe enforcement and verified final disposition. |
| P17 | User-friendly UI and implementation verification hash | **Partial.** Hashes, attested client, public manifest and recorded visualization exist. `ui/` contains a README, not a plan maker. | Integrate separately owned UI for drafting, review, signing, preview, register/submit/report and appeal; tamper tests. Display a verified hash and its provenance, not an unverified server claim. |

## Box requirements

| ID | Required behavior | Current status and evidence | Remaining acceptance |
|---|---|---|---|
| B1 | Execute only jointly signed content; open-source inspectable image | **Partial.** Signature/AST admission and image pins enforced; reproducible image build and release measurements. Source repository and runtime image are private. Public config alone is not open source. | Publish a reviewed, licensed executor source/build artifact and make image review available to both parties; reproduce the published digest from that source. Do not expose credentials, production logs or unrelated private repository history. |
| B2 | Verify Merkle logger signature/hash chain on each batch | **Partial.** Real cryptography for the authorized synthetic export protocol; no unsigned bypass. Each submission is final. | Native signature/checkpoint adapter and real signed fixtures, including tampered/incomplete batches. Viewer JSONL exports alone contain no such native integrity proof. |
| B3 | Signed query, constrained output, enclave-signed report, multi-prompt query | **Implemented in baseline.** Whole text crosses prompts; actual distributed-evidence live case. Report binds run, boot, input, plan and policy. | Extend the report version deliberately for selection, agent and appeal evidence; preserve validation and privacy constraints. |
| B4 | Attested confidential CPU and GPU; protected CPU–GPU communication | **Implemented for the reviewed distributed architecture, subject to its trust dependencies.** CPU Tinfoil CVM verifies separate inference enclave before text transmission; pinned TLS plus provider confidential GPU environment. Host is AMD SEV-SNP, not a claim of Intel TDX. | Keep verification on every approved model route, reject changed release/key and no-CC configurations; audit provider proof coverage. Network-distributed CPU/GPU introduces two enclaves and a network dependency, not literally one VM. |
| B5 | Input filtering according to signed plan | **Missing.** Out-of-scope signed windows are rejected; that is not content filtering. | Implement P9 after authenticating source; retain evidence of full source and deterministic selection. |
| B6 | Encrypted disk and logger→CVM→GPU paths | **Partial.** Attested client→box and box→inference TLS are implemented. Application uses read-only root and tmpfs, not persistent raw-log storage. Native logger transport is untested. | Verify actual logger uses the attested receiving channel. Define/test encrypted persistence for retained appeal/run state, including key release/recovery and rollback handling; no claim that tmpfs proves physical disk/swap encryption. |
| B7 | Wipe disk and memory after audit | **Missing as stated.** Clearing Python references is not zeroization. CPU service and GPU inference service remain alive. There is no erase acknowledgment/proof. | Explicit erasure boundary covering CPU request/parser/program copies, temporary storage, inference tokenizer/response/KV/prefix state, crashes and canceled runs. Need provider-supported verifiable destruction or isolated workload lifecycle. Application-side cache settings or a CPU restart alone cannot prove remote GPU erasure. |
| B8 | Agent scaffold, signed tools, safe sandbox | **Missing.** AST restrictions protect the baseline, but implement no agent loop. | P14 plus resource/time/call/output limits, no tool outside signed allowlist, prompt-injection/escape/exfiltration tests, cancellation cleanup, and actual deployed acceptance. |

## Hardware/lifecycle issue that code alone cannot close

Tinfoil's [Security and Privacy FAQ](https://tinfoil.sh/security-and-privacy-faq)
(updated October 12, 2026; checked October 15) describes temporary prompt-prefix
state in inference-enclave GPU memory; it is volatile, not disk-backed, and is
lost on enclave restart. This does **not** demonstrate immediate per-run erasure
on the currently pinned serving release. Its [prompt caching design](https://tinfoil.sh/blog/2026-07-14-secure-prompt-caching)
explains cache isolation, which is a different property from erasure.

Our direct, release-pinned serving path must be reviewed specifically; general
provider documentation alone does not establish which caches that release enables.
Even disabling prefix caching would not prove every plaintext buffer is zeroized.
The completion decision needs a provider-supported erase/isolation guarantee or a
controlled confidential GPU deployment with a reviewed destruction lifecycle.
A dedicated GPU may be one solution, but is not assumed necessary without that review.
No GPU purchase or new paid instance is part of this audit.

Appeals and erasure must be designed together: clients can resubmit authenticated
original/corrected text under signed appeal rules rather than retaining raw text
indefinitely. Persist only the encrypted minimum state needed to bind deadlines,
attempt counts, original results and final dispositions. A report cannot claim
a future erase event has already happened; any destruction receipt needs a defined
trusted observer and binding to the run/resources it covers.

## Implementation order and required acceptance

1. **Freeze the extended contract.** Version the plan/report/manifest schemas;
   keep v1 single-classifier/no-appeal behavior intact. Define selection, model set,
   random selection, tools, budgets, appeal eligibility/window/method, result schema
   and lifecycle as signed fields. Preserve text ingress. Baseline and extended
   modes resolve the original no-appeal versus optional-appeal wording explicitly.
2. **Selection and model registry.** Authenticate complete source first; apply a
   bounded deterministic selection. A minimal text selection can use jointly signed
   UTF-8 byte ranges with strict boundary checks; metadata-based selectors require
   the logger's authenticated metadata. Review at least two actual model artifacts
   and endpoints; choose only within the signed set using enclave CSPRNG. Test
   forbidden models, caller-influenced randomness, empty selections and no fallback.
3. **Agent runtime.** Implement a bounded tool loop. Initial tools should read/search
   only the verified selected text and call approved classifiers. Tools get explicit
   capabilities, never arbitrary shell, network or filesystem access. Enforce
   budgets outside model control and validate the final result independently. A
   constant scripted classifier is not evidence that the agent path works.
4. **Appeals and lifecycle.** Implement immutable original reports, signed appeal
   policies, authenticated corrections or secondary prompts, links/deadlines/attempt
   counts, crash-safe enforcement and schema-limited final disposition. Complete
   encryption and verified erasure only after choosing a supportable CPU/GPU
   lifecycle. Test replay, restart, expiry, scope widening and partial cleanup.
5. **Publish and integrate.** Prepare licensed, sanitized public executor source and
   reproducible build; connect the other owners' actual UI/logger. UI must not receive
   raw logs unless the agreement permits it. Exercise independently held party keys.
6. **Full acceptance deployment.** Run the single-classifier regression suite and
   real confidential multi-model/agent/filter/appeal/cleanup cases; archive signed
   evidence, fresh attestation, evaluation results and public build provenance.
   Keep the overall status incomplete until every row has matching evidence.

Missing inputs: UI implementation repository/branch; native logger's proof format,
trusted public key and signed valid/invalid fixtures; intended source license;
provider evidence/control for erasure. Additional model endpoints must be discovered
and verified, not mocked as multiple models. These external dependencies do not
excuse the remaining application implementation listed above.

## Audit validation

`make check` on the reviewed implementation: **554 passed, 7 skipped**. This is
baseline/regression coverage, not tests for the missing features. Existing v0.1.3
live acceptance evidence is in
[`static/demo/audit/tooling-evidence/release-v013-acceptance.json`](../static/demo/audit/tooling-evidence/release-v013-acceptance.json),
with the release journal alongside it. Historical evidence is kept immutable;
it must not be relabelled as coverage of the new mandatory extensions.

A fresh read-only attested connection during this audit verified v0.1.3 and ready
health, with boot `3c2e4e1a8323460c96e75f4212130597` matching the saved acceptance
identity. All three saved jointly signed plans and five signed report envelopes
were reverified against configured trust, their plan/input/run/boot bindings and
output schemas. No new inference or production-transcript download was needed.
This verifies baseline evidence, not any missing extension.
