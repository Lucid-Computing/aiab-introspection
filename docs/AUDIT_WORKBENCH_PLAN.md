# Audit workbench product and implementation plan

Date: November 5, 2026. Branch: `dev-astra-one`.

Status: consolidated proposed product plan, incorporating four completed Sol planning reviews and inspection of baseline commit `368dcb9`. The existing worktree is isolated; the capabilities and demonstrations below are acceptance targets, not claims of implementation. The auditor model must be DeepSeek served through verified Tinfoil inference. Pi is the provisional agent harness, subject to the integration gate below.

## Product objective

Build a workbench in which an auditor can formulate a consequential question, assemble and negotiate the evidence and method, test the investigation, obtain the required approvals, and run it confidentially. A reader should understand what the audit can inspect, how it can investigate, what constitutes a finding, what is still unknown, and what each recipient will learn.

The audience includes policymakers, frontier lab researchers and employees, management, independent auditors, and the people whose records are being examined. A successful demonstration lets each audience exercise the system rather than merely watch a prepared result. Named prospective interviewees are intended users, not assumed endorsers or sources of requirements.

The standard comes from investigative auditing and experimental evaluation:

- Start with original events, artifact versions, access records, experiment definitions, and executed actions. Preserve links to the original bytes.
- Define the decision the audit serves, the competing explanations, the evidence needed to distinguish them, and the information each recipient is authorized to receive.
- Use executable joins, temporal queries, code and artifact tracing, controlled experiments, an investigating agent, and independent verification of execution.
- Judge success against hidden scenario ground truth, deterministic checks, held-out real cases, and whether new users can correctly explain and modify a plan. A persuasive model-written report is not the acceptance criterion.

## Four flagship demonstrations

These are complete investigations built from reusable primitives. They are the first demonstrations of the full capability set; the coverage matrix below remains the product backlog.

### 1. Investigate a red-team campaign

Question: did a campaign respect its scope, did agents coordinate in ways that affected its results, and did the additional compute produce new validated findings?

Use a controlled campaign with several agents, explicit rules of engagement, tool and network events, shared artifacts, communication records, outcome checks, usage, and costs. Include an out-of-scope attempt, a blocked attempt, authorized coordination, a misleading similarity between independent attempts, and a long unproductive loop. Some evidence should cross session boundaries.

The auditor follows an observed action into another session, checks the applicable policy version, runs code to deduplicate attempts, seeks counterevidence, and reports scope findings, interaction paths, unique validated outcomes, and marginal yield. A separate matched experiment tests performance changes when evaluation cues or incentives change; this supplies evidence about strategic underperformance rather than pretending that a long transcript proves intent.

Live audience interaction: change the scope boundary or stopping criterion, inspect the plan difference, evaluate the changed plan on a hidden variant, and compare its findings. Remove one communication log and watch a previously supported conclusion become inconclusive. The report must say which conclusions survive.

### 2. Reconstruct how a change reached a release

Question: what did agents contribute, what did people verify, what was shipped, and did sensitive or incompatible material enter the process?

Trace a requirement through a discussion, agent session, file edits, commits, review, CI, and a release artifact. Use actual instrumented toy repositories, reproducible builds, fake credentials, synthetic customer data, and known license fixtures. Include a squash merge, a cherry-pick, similar code independently written, a copied snippet, a later fix, and a review performed outside the primary review interface.

Show an artifact timeline and an evidence-backed account of agent contribution, authorized versus unrequested changes, observed human review and tests, information exposure, and license provenance. A reviewer validation task can test knowledge of a changed subsystem; approval speed or a page-open event alone cannot establish comprehension.

Live audience interaction: challenge an apparent missing review by supplying an authenticated external-review artifact. The system preserves the original result and produces a linked correction. A vendor sees only the contracted release findings, while an authorized investigator sees the supporting references.

### 3. Run an employer policy audit with employee control

Question: did an explicitly defined class of confidential information cross an agreed boundary during a specified period?

Employees inspect the exact policy, their included data, allowed tools, report fields, recipients, period, and retention rules. The frozen employee population must all approve the exact agreement before the run starts. Employer and employee accounts are distinct, and an employee can audit their own records separately.

Sensitive inputs travel from an approved submission client or collector into the confidential runtime. The employer UI receives the authorized report, not transcripts, search results, hidden agent reasoning, or an unrestricted debugging feed.

Live audience interaction: one employee declines and admission fails; changing the policy invalidates approvals; requesting individual raw records fails; a valid aggregate audit runs; a repeated query that would reveal a suppressed individual result is rejected by the release policy. Employees can see the agreed record of who requested, ran, and received the audit.

This is the demonstration of privacy enforcement. Merely removing a Logs navigation link or running the language model in an enclave is insufficient.

### 4. Audit organizational decisions and reported metrics

Question: do the recorded decisions, consultation, treatment of people, and reported delivery metrics match the stated process?

Use a coherent fictional organization with public and private discussions, document history, meeting invitations, decisions, project assignments, role changes, reviews, delivery evidence, and an explicit identity map. Include a policy change, an apparent contradiction resolved by timing, a consultation omitted before a decision, and both confounded and deliberately unequal outcome patterns.

The agent reconstructs chronology, checks claim versions, joins commitments to delivery, computes prespecified cohort comparisons, and searches for alternative explanations. It reports evidence of process discrepancies and statistical associations at their actual strength. Controlled synthetic mechanisms establish whether the system can detect planted unfairness; observational workplace data alone does not turn correlation into a causal finding of discrimination or retaliation.

Live audience interaction: select an alternative cohort or correction, see how the analysis and authorization change, and inspect which findings are supported, contradicted, or unresolved. The same source universe supports diligence, billing, information-barrier, attribution, and knowledge-concentration audits.

## The builder experience

The primary editor is a readable audit document. The graph and code remain available as precise technical views of the same plan.

### A plan reads like an agreement

Every plan has six visible parts:

1. **Question and criteria.** The decision being supported; definitions; examples and counterexamples; possible outcomes; what would make the answer inconclusive.
2. **Evidence.** Sources, subjects, dates, artifact versions, fields, identity mappings, exclusions, and required coverage. Show available versus required evidence before authoring the method.
3. **Investigation.** Ordered, named steps with plain-language descriptions, typed inputs and outputs, and expandable settings. Branching, iteration, and adaptive agent steps are visible.
4. **Checks.** Counterevidence requirements, reference validation, quantitative tests, missing-data behavior, and evaluation cases.
5. **Disclosure.** The exact recipient views, allowed excerpts, aggregate fields, suppression rules, and cumulative release policy. Show realistic examples of permitted and forbidden outputs.
6. **Approval and operation.** Approvers, scope of consent, model and runtime, budgets, run frequency or maximum run count, retention, cancellation, and challenge procedure.

Example readable plan:

> Investigate whether campaign R17 tested targets outside the approved scope.
>
> Use the campaign's signed scope history, agent sessions, tool executions, network events, and shared artifacts from the agreed period. Follow links between participating agents only.
>
> Reconstruct each candidate action, identify the policy effective at that time, and check whether it was attempted, blocked, or completed. Search for later authorization, test-environment aliases, and other explanations before finding a violation.
>
> If an essential execution or scope record is missing, mark that finding inconclusive. Do not infer that no violation occurred from an empty search.
>
> Give the lab an aggregate result. Give the authorized investigator a finding list with approved evidence references. Use DeepSeek through verified Tinfoil inference within the signed budget. Changing these permissions requires new approval.

### Author and negotiate

Support starting from a question, a worked audit, or a blank plan. An authoring assistant suggests executable changes and explains what data is missing. Each suggestion is a visible semantic diff: added sources, widened subject scope, changed decision criteria, additional capabilities, new disclosure, and changed cost. Applying a suggestion does not sign it.

Make definitions directly editable: selecting “my agents,” “before approval,” or “delivered” opens the corresponding identity, temporal, or acceptance rule. Clicking a readable clause highlights its graph operators and fields. Let people compose methods using verbs such as Find, Link, Reconstruct, Compare, Investigate, Measure, and Release. Accept independent suggested edits separately; changes that depend on one another travel together.

Use deterministic descriptions of executable settings for the authoritative agreement view. Model-written explanations may help, but must not silently replace or contradict those settings. Unsupported custom code is shown explicitly as custom code with its input/output contract; the UI must not claim to fully explain its semantics.

Provide inline comments, counterproposals, named review responsibilities, and resolved versus unresolved questions. Show a consent preview from each recipient's perspective. Evaluation results and approvals attach to an exact plan digest; a material edit invalidates them. Cosmetic layout changes can remain outside the executable commitment.

### Test and debug

Replace the current mock simulation with real execution on approved sample data. Offer fixture selection, positive/negative/ambiguous cases, per-step inputs and outputs, source coverage, failed joins, citations, costs, and agent actions. Step through deterministic transformations; inspect and rerun an agent step with a changed hypothesis on synthetic or otherwise authorized evidence.

Expose three separate actions: **Check plan** validates the contract, available evidence, permissions, runtime compatibility, and budgets; **Rehearse** executes the actual methods on permitted fixtures; **Evaluate with DeepSeek** measures behavior against labeled cases through the required inference route. Trace population → selection → links → claims → conclusions → released fields, showing exclusions and denominators at each step. A syntactically valid future plan may be saved as a draft while clearly marked incompatible with the deployed runtime.

Real-data debugging is subject to the same access and disclosure policy as a run. A private execution trace is not automatically visible to the plan author. Support synthetic full-trace mode, investigator-authorized evidence mode, and restricted aggregate mode as explicit choices.

### Read and challenge results

The result starts with an answer, its scope, and unresolved questions. Each finding links to supporting evidence, counterevidence, method, coverage, and the relevant approved rule. Distinguish execution failure, insufficient evidence, supported finding, contradicted hypothesis, and a qualified negative finding.

Use explicit states for suppressed results and blocked runs. A suppressed count is never displayed as zero; an abstention is never silently counted as a clean negative. Distinguish observed facts, reconstructed relationships, statistical associations, and causally supported conclusions in both findings and summaries.

Provide timeline, table, artifact-lineage, and cohort views where they help answer the question. A separate verification view exposes plan and dataset commitments, approvals, runtime/model identity, and an independently verifiable receipt. A challenge adds an authenticated correction or an approved secondary analysis; the original report stays immutable.

A challenge identifies the disputed claim, supporting correction, requested remedy, and permitted recipients. Its disposition is upheld, amended, withdrawn, or unresolved, with deadlines, attempt limits, and reviewer-conflict rules where relevant. A changed method requires renewed authorization and disclosure accounting. Notify authorized recipients of an amended finding without exposing private challenge material.

## Reusable execution primitives

| Primitive | Required behavior |
| --- | --- |
| Evidence source | Versioned dataset manifests; typed events and artifacts; source-native metadata; ingestion and coverage status; authorization labels. |
| Select and project | Exact subject, time, field, artifact, and policy selection, enforced before exposing data to a tool or model. |
| Identity and artifact resolution | Explicit identity maps with evidence and uncertainty; commit/file/document/session lineage; ambiguous matches remain ambiguous. |
| Join and time comparison | Exact joins, bounded fuzzy candidates, temporal joins, event sequences, before/after windows, and policy versions effective at an event. |
| Extract and classify | Structured extraction and classification with source spans, counterexamples, evaluation evidence, and abstention. |
| SQL and Python analysis | Read-only relational analysis and isolated computation over explicit authorized inputs; code and dependencies are versioned and reviewable. |
| Cohort and experiment analysis | Denominators, uncertainty, matched comparisons, randomization where available, confounders, missingness, and multiple-comparison handling. |
| Auditor agent | Adaptive search, artifact tracing, code execution, hypothesis testing, and counterevidence investigation under a signed mandate. |
| Coverage and absence check | Machine-checkable definition of the observed universe; collection gaps; searched versus examined evidence; explicit conditions required for a negative claim. |
| Assertions and validation | Reference integrity, schema checks, deterministic recomputation, contradiction checks, and declared evidence requirements. |
| Disclosure and approval | Recipient-specific release, purpose/subject scope, required approvals, limits on repeat releases, and challenge rules. |
| Evaluation | Named fixture sets, hidden labels, false positives/negatives, abstention, citation validity, disclosure tests, and run cost. |

Introduce a versioned contract shared by the Python runtime and TypeScript builder. Generate compatible schemas/types where possible and maintain contract fixtures accepted or rejected identically by both. Preserve existing signed plans under their existing semantics; migration produces a new unsigned plan and a reviewable difference.

The main durable objects are **Dataset**, **Plan**, **Approval**, **Evaluation**, **Run**, **Finding**, and **Disclosure**. Each has a stable ID, version/commitment, provenance, and an explicit access policy. Large evidence artifacts live behind committed references rather than inflating the signed plan. The signature must bind every executable external resource and policy that can affect a run.

Agree these interfaces before parallel implementation:

| Boundary | Contract |
| --- | --- |
| Builder to compiler | Plan package → normalized executable policy, readable clauses, typed diagnostics, and required runtime capabilities. |
| Runtime to builder | Supported schema/operator versions, admitted model profiles, tool versions, source adapters, and limits. |
| Collector to admission | Source snapshot → authenticated origin, payload commitment, observed-universe/coverage manifest, transformation versions, and encrypted payload reference. |
| Consent to admission/release | Approval set → exact plan/data/policy bindings, authoritative roster, validity and revocation state. |
| Agent to broker | Capability request → operation, purpose, scoped references, parameters, and budget reservation. |
| Broker to agent | Authorized result or typed denial, actual resource accounting, provenance, and protected trace commitment. |
| Execution to release | Private findings, claim graph, coverage and execution result → independently validated recipient packages. |
| Independent verifier | Verification bundle → distinct checks for authorization, submitted bytes, asserted coverage, runtime/model identity, and release conformance. |

Keep structural and semantic conformance fixtures shared across editor, server, compiler, and runtime. Include empty populations, invalid source substitution, ambiguous identities, insufficient evidence, suppression, cancellation, restart, stale consent, and tampering. A valid signature remains meaningful for the original schema; do not reuse a historical schema identifier for a new incompatible contract.

## Evidence architecture and full audit coverage

Preserve both source-native records and normalized events. Each normalized event carries a source and native ID, actor/agent identity, event and ingestion times, session/campaign/project, action type, artifact/version relationships, policy context, content or content reference, classification, integrity evidence, and normalization version. Record uncertainty and clock quality rather than fabricating exact ordering.

Source snapshots include the collection scope, connector version, source permissions, pagination/checkpoint state, time bounds, omitted or deleted content, collection errors, and duplicate handling. Hashes prove committed bytes; they do not establish that every real-world event was collected. Negative findings require an agreed universe and sufficient collection coverage for the specific question.

The observed universe is defined independently of the returned records: accounts, repositories and branches, channels, devices, endpoints, action types, and period. Require opening/closing checkpoints or precise weaker assertions, empty-partition evidence, key epochs, and collection exclusions. Reconcile with an independent inventory where available. Preserve conflicting duplicate versions and parsing failures instead of silently choosing the latest or skipping malformed input.

Replace presentation-oriented truncation with lossless raw retention and bounded, explicit retrieval. The current proxy conversion cuts request/response text at 8,000 characters and tool text at 2,000; it also normalizes Slack/GitHub text into proxy-shaped records. Those projections must not become the only evidence for a substantive audit. Current signatures authenticate selected submissions, including exports produced with the demo logger key; they do not establish native WORM completeness.

For large datasets, introduce snapshot manifests, partition commitments, and a scoped query/index layer. Cross-partition joins, campaign state, and absence checks operate over the declared whole snapshot. Running unrelated 2,000-record classifiers and concatenating their answers is not an adequate scale architecture.

| Original audit | Evidence or new instrumentation to build | Analysis and defensible result |
| --- | --- | --- |
| Agent reconciliation and unrequested work | Agent action traces, user instructions and amendments, workspace edits, commit/rebase lineage, review artifacts. | Trace contributions and action authorization through to shipped changes; distinguish generated, edited, reviewed, tested, and unattributed portions. |
| Secrets, customer data, and confidential-document exposure | Fake-secret fixtures for demos; real authorized data classifications, document fingerprints, prompt/tool ingress, destinations, and access grants. | Detect exact/derived material and trace where it went, under which policy; avoid reproducing secrets in results. |
| Promise ledger | Thread versions, explicit assignees/dates, issues, deliverables, acceptance and cancellation records. | Resolve commitments to fulfilled, superseded, canceled, overdue, or uncertain; closure alone is not delivery. |
| Idea and document attribution | Versioned documents, messages, artifact links, citations, access and reuse history. | Earliest observed provenance and unattributed reuse candidates; similarity alone does not establish theft or original invention. |
| Attention and project time | Opt-in activity intervals, session/project mapping, idle periods, explicit work boundaries, task outcomes. | Personal activity distribution and overlap with clear clock/idle assumptions; wall-clock span is not hours worked. |
| Review quality | Diff/version exposure, review comments, reviewer-authored tests, defect fixtures, later defect outcomes, external-review records. | Evaluate observable review practices and defect detection, controlling for change size and difficulty; approval time is one signal. |
| Human understanding and real ownership | Review evidence plus voluntary explanation, change-impact prediction, debugging, or maintenance tasks with independent checks. | Measured understanding of a subsystem at a specified time; no claim to prove an internal mental state or that nobody ever understood it. |
| Bus factor and knowledge concentration | Contribution/review history, ownership map, on-call incidents, documentation, maintenance exercises. | Subsystem-specific concentration and demonstrated coverage; avoid equating commit count with knowledge. |
| Document opened before a meeting | Versioned document share/access events, recipient identity, meeting timing, offline copies and source coverage. | Establish an observed access event before the decision; absent events require complete relevant instrumentation, and opening is not reading. |
| Leadership private/public consistency | Authorized private and public statements, timestamps, policy versions, scope/audience context, superseding decisions. | Identify materially inconsistent claims after checking timing, scope, and subsequent changes. |
| Decision provenance and consultation | Decision records, discussion threads, meeting attendance/invitations, consulted-party responses, affected-party roster. | Reconstruct who proposed, considered, approved, and announced a decision, and which required consultations have evidence. |
| Promotion or review fairness | Synthetic personnel and outcome records first; approved real cohorts, role/level/tenure/work measures, evaluation criteria and communication graphs. | Prespecified descriptive and adjusted comparisons with uncertainty and alternative explanations; controlled synthetic bias tests; human adjudication of adverse implications. |
| Retaliation indicators | Concern timing, assignments/access/invitations before and after, documented reasons, comparison cohorts, organizational changes. | Detect treatment changes associated with a concern and check competing causes; observational findings remain indicators unless a stronger design supports causation. |
| Agent-written performance reviews | Document edit lineage, authorized agent output receipts, revision history and reviewer actions. | Attribute observable model assistance and subsequent human editing; do not use writing-style AI detection as proof. |
| Vendor credentials and customer-data handling | Credential grants/scopes, vault access, agent identities, tool/network events, dataset and document movement, revocation. | Distinguish credential availability, actual use, and downstream exposure against contractual boundaries. |
| Code/model provenance and license concerns | Agent model receipts, edit/commit/build lineage, dependency lockfiles, SBOM, source/license fixtures, copied-code candidates. | Explain provenance and candidate incompatibilities for review; authorship percentages specify the unit and uncertainty, and legal conclusions require separate judgment. |
| Billing integrity | Contracted billing units, usage invoices, task executions, accepted deliverables, recorded labor where authorized. | Reconcile quantities and duplicate/missing charges against the contract; agent latency is not a human timesheet. |
| M&A diligence and metric integrity | Release and delivery evidence, issue history, incidents, technical-debt samples, ownership exercises, published metric definitions. | Recompute reported metrics, document exclusions, and inspect delivery quality and concentration; no universal proxy for company value. |
| NDA and information barriers | Policy versions, allowed populations, document classifications, actual access and transfer paths. | Track material across prohibited boundaries and explicit exceptions; inspect content and provenance rather than channel names alone. |
| Who audited whom and why | Authenticated requests, stated purpose, approvals, plan/data commitments, run and release events, recipient identities. | Query an append-only accountable history; audit its coverage and detect unapproved or altered runs. |
| Agent coordination | Agent messages, shared files, task assignments, tool interactions, causal experiment assignments. | Trace actual information exchange and downstream behavior; test coordination hypotheses against independent common-task behavior. |
| Sandbagging or slacking | Repeated matched tasks, task difficulty, known capabilities, randomized evaluation cues/incentives and outcomes, complete action traces. | Test reproducible conditional underperformance; intent requires stronger evidence than performance differences. |
| Red-team scope adherence | Signed rules of engagement, target identities/aliases, amendments, requests, executed tools and network results. | Classify authorized, attempted, blocked, completed, and inconclusive actions against the contemporaneous scope. |
| Loops and repeated ideas | Full campaign steps, canonical tool arguments, failure states, artifact deltas, attempts and hypotheses. | Detect cycles and duplicate attempts while distinguishing useful retries and independent validation. |
| Long-campaign value | Validated unique findings, attempt clusters, compute/tokens/cost, stopping rules, repeated campaign seeds. | Estimate marginal verified yield and compare policies over matched runs, with uncertainty. |
| One/two versus four/five parallel sessions | Opt-in crossover or randomized task blocks, difficulty, quality checks, human review effort, wall time and model cost. | Compare throughput and quality under specified conditions; session count alone cannot establish productivity. |
| Self-only and unanimously agreed employer audits | Verified identity ownership, participant roster, signed purpose/scope/output agreement, consent and revocation events. | Enforce personal scope or required collective authorization in admission and every tool; expose only the agreed recipient projection. |

## Dataset program

### A coherent fictional organization

Create Northstar Systems, a reproducible fictional enterprise agent vendor with a research group. An initial target is 48 fictional employees, 12 service/agent identities, six repositories, three product projects, four customers, three vendors, and two information compartments over 16 weeks of detailed events. Add a longer personnel/decision history and larger cohort variants for promotion, retaliation, and diligence cases; this small world cannot support strong general statistical claims. These are engineering targets, adjustable after measuring whether they provide enough cross-source relationships and cohort variation.

Generate structured facts first: identities, roles, decisions, tasks, policies, artifact versions, access grants, contributions, outcomes, and a causal scenario ledger. Render those facts into Slack-like threads, Git histories, documents, issue trackers, access logs, invoices, personnel tables, and agent traces. Model-written natural language can add realism, but constraints and independent validators must preserve the facts.

Keep the ground-truth ledger outside the audit environment. Labels and scenario names must not leak the answer through filenames, metadata, repeated phrases, or IDs. Include everyday activity and benign lookalikes so searching a rare suspicious word does not solve the whole audit.

Maintain three separate stores: **what actually happened**, **what the sources observed**, and **what the auditor is justified in concluding from the permitted observations**. A real violation with missing decisive evidence should sometimes be labeled inconclusive. Independent annotators adjudicate interpretive cases and retain disagreement. The auditor never receives the hidden world, expected answer, or attacker objective.

### Cases with adversarial variants

Start the first integrated slice with a supported finding, clean negative, benign lookalike, and missing-evidence case. The full benchmark target is two independently authored scenario families for each of the 27 coverage rows, each with eight variants: supported finding, compliant behavior, suspicious but benign behavior, ambiguity, contradiction, missing decisive evidence, integrity failure, and changed permission/consent. This yields 54 families and 432 initial case instances. The count provides systematic engineering coverage, not statistical certification. Add identity, chronology, and prompt-injection variants where applicable. Split by scenario family and seed, not randomly by individual record, so correlated templates do not leak across evaluation sets.

Maintain a small presentation dataset, a broader development set, a hidden evaluation set, and a scale dataset. Proposed scale gates are 100,000 and then one million events after correctness works on small cases. Large datasets test search, joins, pagination, cost, and provenance; their size is not evidence of scientific validity.

### Real executions in controlled worlds

Instrument agents working in toy repositories and sandbox services. Run real campaigns, generate actual diffs and reviews, perform harmless mock data transfers, and validate outcomes programmatically. Record model/runtime identities, costs, tool effects, and failures. Use fake credentials and invented personal/customer data.

For sandbagging and productivity questions, define interventions, randomization, task matching, and repeat counts before execution. A scripted agent with planted behavior is useful for plumbing tests; a real model choosing behavior under a controlled intervention is a different dataset and must be labeled separately.

A proposed campaign target after model qualification is 12 task/attack families with five seeds and paired attack/control conditions: 120 sandbox runs. Measure legitimate task success alongside attack outcomes, and keep stopped and failed runs. Start smaller to debug the harness; expand only after the environment and scorers are validated.

Use pilot variance and the intended effect size to choose the sample size for sandbagging or productivity experiments. For fairness, separately test a model evaluator with matched fictional dossiers whose randomized condition changes while work evidence stays fixed. Such an experiment measures that evaluator under those conditions; it does not establish a real employer's causal fairness. Freeze the analysis before opening the held-out results.

### Reuse external evaluation environments

Adapt selected [AgentDojo](https://github.com/ethz-spylab/agentdojo) cases to test prompt injection through retrieved evidence and tool results. Use [Petri](https://www.anthropic.com/research/donating-open-source-petri) as a source of controlled model-behavior experiments and transcript formats, with its generators and judges kept separate from our audit evaluator. Preserve the licenses, exact versions, provenance, and evaluation conditions of imported cases. Neither environment replaces the company corpus, the source-integrity tests, or independent labels for our audit claims.

### Authorized real pilots

Define a versioned import/export contract for WORM/Amber campaign data with the owners: session and campaign IDs, policies, tool calls/results, agent communications, artifacts, outcomes, usage, timestamps, and integrity/coverage evidence. Do not invent an integration API or assume the planned export is already available.

Native acceptance needs valid and invalid fixtures: empty signed intervals, boundary events, key rotation, missing segments, bad signatures, duplicates, delayed events, replay, forks, rollback, and permission-limited coverage. Verify native proofs before filtering or summarizing, preserve source encoding and positions, and bind the adapter transformation. Until this passes, label source authenticity and coverage precisely rather than upgrading a successful import into “complete.”

Add opt-in personal coding-workflow pilots and an explicitly approved organizational pilot after identity, consent, and disclosure controls work. Real datasets remain separate from public synthetic demo data. De-identification alone is not a permission grant, and a synthetic-derived accuracy score does not establish performance on the real pilot.

## DeepSeek auditor runtime

Tinfoil currently lists `deepseek-v4-1-flash` with tool calling and structured outputs, and marks it experimental. Treat it as the candidate endpoint; select and verify an actual serving release before freezing the model profile. The signed plan binds the approved model, weights/serving identity, inference configuration, harness, tools, and budgets. [Tinfoil model catalog](https://docs.tinfoil.sh/models/chat).

Use Pi provisionally for the adaptive loop, running as a disposable process supervised by the confidential executor. Pi supports explicit tools, session state, resource loading, and execution events. Its integration must disable ambient project configuration, skills, credentials, and cross-audit memory. [Pi SDK](https://pi.dev/docs/latest/sdk).

The preferred path is an inference broker outside the agent process that enforces the model profile, verifies Tinfoil attestation and release bindings, and accounts for every call. Pi can use a compatible broker transport or a reviewed custom provider. A base-URL change alone does not meet the requirement. Tinfoil documents verified SDK transport and an attesting proxy for compatible agents. [JavaScript SDK](https://docs.tinfoil.sh/sdk/javascript-sdk), [coding-agent integration](https://docs.tinfoil.sh/tutorials/coding-agents).

The supervisor owns credentials, approval verification, evidence capabilities, budgets, cancellation, and report signing. The agent gets search, fetch, joins, read-only SQL, isolated Python, artifact tracing, and evidence/coverage registration. It can pursue hypotheses and counterevidence, but cannot widen its dataset, change its policy, authorize a new recipient, or create an unrestricted network route. Bounded delegation can be added with inherited or narrower capabilities and one shared budget.

All inference paths, including context compaction and summaries, use the approved DeepSeek/Tinfoil route. Semantic retrieval initially uses local approved computation; any later external embeddings or reranking require their own approved confidential model profile. No silent provider fallback is permitted. Failed verification stops sensitive transmission, including on retries and re-attestation.

Audit extraction, classification, and report synthesis follow the same DeepSeek requirement. The existing authoring chat is a separate Anthropic path: keep protected evidence out of it and move evidence-aware authoring to the approved DeepSeek/Tinfoil route. Public, evidence-free drafting must still make its provider and context boundary explicit.

Private structured findings contain a claim, outcome, evidence references, counterevidence, unresolved alternatives, coverage limitations, method, and validation status. The execution trace records actual calls and actions; a generated reasoning narrative is not proof of the model's internal process. Citations must resolve to authorized immutable records, and independent checks test whether those records support the conclusion.

The current Python worker blocks process creation and sockets, and the broker serves each model node only once. Add a new agent isolation/protocol boundary rather than relaxing the existing worker globally. Expand plan/report limits through a versioned contract with measured resource budgets. Keep deterministic execution replay separate from rerunning stochastic inference.

The first spike must establish real multi-turn tool calls, verified model identity, context/reasoning handling, output validity, cost accounting, cancellation, and failure on changed identity. Review SDK recovery behavior and the actual serving release. Tinfoil labels V3 workload pinning/freshness and related features beta; document the exact verification path used. [Feature status](https://docs.tinfoil.sh/resources/feature-status).

## Confidentiality, consent, and accountability

Provide three explicit modes: personal investigation, jointly authorized investigation with specified evidence visibility, and confidential policy audit with restricted recipient reports. Mode is an enforceable policy object, not a display preference.

Identity ownership and data authorization are checked at admission and by every query/tool. A display-name filter is insufficient for personal-only access. For collective consent, freeze the required participant roster and its authoritative source, explain exclusions, and require the agreed quorum; the employer demonstration requires every employee in the agreed organization roster. It must not shrink that roster merely to bypass a refusal. Changing subjects, purpose, policy, recipients, or disclosure requires new approval. Revocation blocks future runs and undisclosed results under a defined policy; already released information cannot be recalled.

Private self-audits are not discoverable by employer accounts, and an employee's conversation is not unrestricted permission to disclose another person's data. Refusal and revocation are private; employer views show only the minimum readiness state allowed by the policy, not who declined or a small-cohort participation count. A signature does not by itself establish voluntary consent, so include an independent challenge route and a stated process for handling coercion concerns.

Place raw inputs, indexes, working files, agent context, and private traces inside the confidential execution boundary. Collectors or an independently trusted submission client verify the recipient enclave before delivering data. An ordinary UI backend forwards ciphertext or metadata only. A browser frontend controlled by that backend is not, by itself, a defense against the backend serving malicious submission code; the trust model must identify the client and its update verification.

Separate private findings from released reports. The release service enforces recipient fields, approved excerpts, group thresholds, query/run limits, and linkage to the agreement. Small-count suppression alone does not prevent reconstruction across repeated or overlapping queries. Support a reviewed restricted-release policy for the demo and a properly accounted differential-privacy mode where its statistical tradeoffs fit the use case; do not label ad hoc suppression as differential privacy.

Revalidate authorization at both admission and release. Persist the disclosure ledger across restarts and accounts; reserve budgets atomically and test concurrent requests, revocation during a run, stale policy, replay, and rollback. A stricter policy may block a pending release; it must not widen a previous grant. Replace in-process daemon-thread orchestration with a durable run lifecycle for these guarantees. Recurring audits, if configured, use signed acquisition rules, bounded run counts, and fresh approval checks on every admission.

Use per-run and per-recipient opaque references where raw IDs would disclose identities. Define retention, encrypted checkpoints, key access, crash handling, deletion, and challenge windows together. Do not promise immediate GPU memory erasure merely because the CPU job ended. A signed report proves a particular execution and release, not that every source was complete or that the model's conclusion is correct.

Record authenticated request purpose, plan and input commitments, approvals, admission/rejection, model/runtime profile, tool-policy violations, completion, challenges, and actual disclosures in an append-only accountability log. Use independent checkpoints or witnesses to make administrator rewrite/rollback detectable. Minimize the ledger's own sensitive metadata and define who can inspect it.

## Existing implementation and migration

The branch already supplies useful foundations: graph/YAML editing, plan validation, signatures, a sandboxed executor, a pinned inference client, log viewers, and run/report verification. It does not yet supply the complete workbench described here.

| Existing surface | Planned change |
| --- | --- |
| `ui/playground/src/plan/` and editor components | Versioned richer plan types; readable document view; typed joins, coverage, agent, evaluation, consent, and disclosure; graph/code projections kept in sync. |
| `ui/playground/src/plan/simulate.ts` | Replace mock-only behavior with a clearly labeled real preview/evaluation service. |
| `ui/server/plan_chat.py` | Semantic proposal differences; explicit authoring-context policy; DeepSeek/Tinfoil for any evidence-aware assistance. |
| `ui/server/plan_view.py`, `app.py`, templates | Guided authoring, semantic differences, review and approval flows, identity-aware authorization, privacy modes, findings and challenges. |
| `ui/server/proxylogs.py`, `slacklogs.py`, `githublogs.py` | Preserve native metadata and provenance; connector manifests; real cross-source datasets; import validation and coverage. |
| `ui/server/runs.py` | Remove host-readable raw input persistence from confidential mode; introduce sealed-input run references and recipient-safe progress. |
| `static/aiab/baked/plan_ir.py`, `bundle.py` | Explicit version evolution, signed resource commitments, multi-party policies, richer findings and release contracts. |
| `static/aiab/baked/executor.py`, `graph.py`, `worker.py` | Agent supervisor/protocol, evidence/query services, analysis sandboxes, resource accounting, cancellation, private trace handling. |
| Classification and result contracts | Distinguish supplied context IDs from actual support spans; represent suppression, abstention, and no finding separately. |
| `static/aiab/audit/model.py`, transport and trust | Reviewed DeepSeek/Tinfoil binding, verified agent inference, retries/streaming/context tests, no alternate route. |
| `static/auditor/`, report verification, deployment | New receipts and verification tooling, measured runtime release, separately pinned ephemeral environment. |

Historical design and completion documents contain older snapshots. Reconcile them against the implemented graph contract rather than treating every historical paragraph as current behavior. Existing signatures must not silently authorize new semantics or new model releases.

## Delivery roadmap and acceptance

Use dependency gates rather than promising a calendar before the model integration and source contracts are established. Five proposed implementation workstreams can build independent parts, but security review, source-owner decisions, and interviews still need people. The four completed planning reviews are inputs to this plan, not evidence that implementation is running. Run formative comprehension sessions on the readable plan in gate 0 and on the first real investigation in gate 2; do not postpone usability feedback until the entire audit portfolio is implemented.

| Gate | Deliverable | Acceptance |
| --- | --- | --- |
| 0. Freeze the contracts | Audit coverage matrix, canonical plan/evidence/finding/disclosure schemas, threat and trust boundaries, fixture generator skeleton, readable plan prototype. | One example round-trips through readable/graph/code views; reviewers identify exactly what changes authorization. |
| 1. Prove the agent path | Disposable auditor with real DeepSeek/Tinfoil, evidence search and fetch, one join, SQL/Python, counterevidence, structured result, independent budget controls. | Hidden cross-session case succeeds; missing evidence changes the outcome; wrong model/attestation and forbidden tools fail before disclosure. |
| 2. Ship one complete investigation | Red-team campaign demo, authoring and real preview, signed approval, real confidential run, findings, independently checked receipt. | A new user edits the investigation without editing backend code and gets a materially different correct result. |
| 3. Prove privacy and provenance | Employee-consent demo and release-provenance demo; sealed ingestion; subject authorization; release accounting; challenges. | Missing consent, policy changes, raw-log requests, repeated-query reconstruction attempts, and cross-user access are blocked; a valid correction produces a linked result. |
| 4. Cover the audit portfolio | Fictional-organization corpus, decision/fairness/diligence demo, remaining coverage-matrix plans, real executed campaigns, held-out evaluation. | Every matrix row has an executable plan, required evidence contract, tests, and a declared validity boundary. No disabled placeholder is counted as coverage. |
| 5. Validate with target users | Moderated sessions with each audience, authorized real pilot, usability fixes, repeatable live demo and labeled replay. | Users explain scope and disclosure, author a new question, recognize insufficient evidence, challenge a finding, and distinguish synthetic from real evidence without presenter rescue. |

Proposed parallel ownership after contract freeze:

- **Builder worktree:** readable editing, negotiation, semantic differences, preview/debug views, result comprehension.
- **Evidence worktree:** normalized/native schemas, connectors, identity/artifact joins, coverage, confidential ingestion interfaces.
- **Runtime worktree:** Pi adapter/supervisor, DeepSeek/Tinfoil binding, tools, isolation, budgets, findings and verification.
- **Consent and disclosure worktree:** authoritative roster, approval state, admission/release checks, durable disclosure budgets, recipient projections, challenge and retention policy.
- **Datasets and assurance worktree:** scenario generator, controlled executions, protected held-out cases, independent verifier tests, consent/disclosure attack tests, demo scripts and evaluation reports.
- **Integration on `dev-astra-one`:** shared contract changes, end-to-end tests, runtime/UI deployment, and documentation. Work stays in isolated worktrees, with narrow commits and explicit ownership of shared schema changes. Freeze evaluation labels and access separately from implementation authors before reporting held-out performance.

### Evaluation gates

Treat these as initial proposed release criteria to ratify before evaluating held-out data:

- Every audit family has clean, positive, ambiguous, and degraded-evidence cases. Report task-level precision/recall, abstention, errors, denominators, and intervals; do not substitute one global accuracy number.
- Every released evidence reference resolves to authorized committed material. High-impact findings require a documented evidence chain and human review when interpretation warrants it.
- No seeded unauthorized disclosure, tool escape, provider bypass, consent bypass, or cross-run contamination is tolerated in the release suite. Passing that suite is bounded evidence, not a universal security guarantee.
- Deterministic calculations match independent oracles exactly. Agent semantic judgments receive blinded review; model self-grading is supplemental.
- In an initial 20-person moderated study distributed across intended audiences, target at least 90% correct answers to scope/recipient questions and 80% successful completion of the core edit-and-preview task without help. Test whether people distinguish verified execution, supported findings, inconclusive results, and suppression. Report by audience, publish the small sample size, and fix any belief that employer mode permits unrestricted raw-log access.
- Interactive authoring and source navigation stay responsive. Proposed small-case rehearsal targets are admission/refusal within two seconds, warm-run p95 within 120 seconds, and local receipt verification within five seconds on declared hardware. Measure cold startup separately. These are unmeasured targets to validate in the DeepSeek spike, not promises; display actual cost, queues, budgets, and failures. Large-corpus audits receive separate throughput/cost targets after measurement.
- Show live execution separately from any recorded verified replay. A replay is a useful recovery path, never evidence that the current live run succeeded.

## Branch environment and demonstration operations

Provision a disposable UI environment tracking `dev-astra-one`, with its own storage, accounts, keys, and synthetic seeds. Because the agent changes the measured runtime, also deploy a separate Tinfoil audit runtime with matching release pins. Reusing the existing dev executor is acceptable only for unchanged legacy plans, not as a substitute for deploying the new agent engine.

Branch pushes build and deploy the correct UI and versioned runtime artifacts through separate release gates. Record the served commit, plan version, runtime digest, model profile, dataset seed, and verification results. Failed compatibility or acceptance checks leave the previous known-good demo intact. Publish a reset procedure and explicit teardown/expiry policy for the ephemeral resources.

The public/presentation environment uses synthetic data by default. Authorized real pilots use isolated data and access controls; copying existing corporate logs or credentials into a demo is not part of the default deployment. Maintain distinct lab, employee, auditor, employer, and verifier roles so the audience can exercise real authorization boundaries.

## First implementation package

Start with the contract fixtures, readable red-team plan, coherent small campaign dataset, and actual DeepSeek/Tinfoil agent spike in parallel. Then join them into a real preview and signed run before broadening the catalog. Build the confidential-ingestion and release boundary early enough that the consent demonstration exercises the same architecture as the remaining audits.

The eventual completion condition is the entire coverage matrix, four interactive demonstrations, independent evaluation, and an isolated branch-tracking deployment. A set of prompts, a polished mock simulation, or signed outputs without evidence correctness does not satisfy this plan.

## Decisions and external inputs

Fixed decisions are the DeepSeek/Tinfoil audit-model route, readable executable agreements, evidence-backed adaptive investigation, explicit recipient control, full coverage of the original audit portfolio, and isolated branch/worktree development. Pi is implemented. A pinned DeepSeek serving release has passed live tool-use qualification through the official Go Tinfoil SDK; this does not qualify the separate confidential audit supervisor deployment.

External inputs are native WORM/Amber samples and trust/checkpoint specifications, authoritative identity and participant rosters, access to permissioned pilot sources, independent annotation/review expertise, and target users for comprehension testing. Synthetic adapters and datasets allow the product work to proceed while these are obtained. Native completeness, real-world effectiveness, and any provider-dependent lifecycle claim remain visibly unvalidated until their own evidence exists.

### Backend governance integration status (November 5)

The authenticated workbench adapter now connects multi-party discovery, exact-version
Ed25519 approvals, independent operator-provisioned policies/manifests, recipient-only
projections, receipt verification, durable clause review notes and challenge review
lineage. Operational input formats and frontend endpoint additions are specified in
[WORKBENCH_GOVERNANCE_API.md](WORKBENCH_GOVERNANCE_API.md). Provisioned authority is
explicitly imported trust; the service creates neither voluntary-consent claims nor
runtime verification facts. Synthetic challenges remain owner-only and unverified.
Confidential admission remains blocked. The runtime now calls an independent
authorization adapter immediately before tokenization, inference and tool access;
the parent UI and confidential factory wire current agreement/source authority into
that adapter. Tests cover revocation and denial independently of private event callbacks.
Live DeepSeek qualification is recorded separately in
`static/agent_runtime/review/integrated-qualification.json`: real inference, synthetic
evidence, two turns, all seven tools and 14,603 tokens. It does not establish native
source completeness, real-world audit accuracy or confidential supervisor execution.

### Integrated implementation status (November 5)

The isolated `dev-astra-one` branch contains the readable document/graph/code builder,
verified draft-only authoring suggestions, deterministic and agent evaluations,
27 executable audit methods, 432 synthetic cases in 54 families, bounded evidence
queries and adaptive Pi investigations, browser-held consent keys, recipient releases,
challenge lineage and local pinned-key receipt verification. Findings expose
counterevidence and coverage; rehearsal findings can open cited synthetic records.
Evaluation reports include family-level metrics with denominators. Synthetic oracle
agreement is not held-out validation.

The separate Render environment is at https://aiab-dev-astra-one.onrender.com/workbench
with its own disk, accounts and session key. It uses real verified Tinfoil inference,
but its evidence-processing host is ordinary Render infrastructure. Confidential
employee audits remain refused there. The production confidential factory, trusted
collector and image packaging are implemented; the independently reviewed authority,
measured release, protected keys/storage and deployment gate artifacts remain required.

Render is configured for the branch, but its repository push integration did not fire.
Manual deployments work. The branch-only GitHub workflow requires the isolated
service's `RENDER_DEV_ASTRA_ONE_DEPLOY_HOOK` secret before automatic deployments work.
Never reuse a dev/prod service hook. Target-user interviews, native WORM/Amber
verification fixtures and independent held-out evaluation remain external work.
