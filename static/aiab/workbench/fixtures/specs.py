"""Versioned evidence contracts; no scenario labels or expected answers."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Spec:
    id: str
    title: str
    types: tuple[str, ...]
    method: str
    limit: str
    families: tuple[str, str]


SPECS = (
    Spec(
        "agent-reconciliation",
        "Agent reconciliation and unrequested work",
        ("instruction", "edit", "release"),
        (
            "Join shipped blob hashes to edits and effective instruction "
            "paths; separate authorship from authorization."
        ),
        "A blob receipt attributes an observed edit, not intellectual authorship.",
        ("release-path", "amended-task"),
    ),
    Spec(
        "information-exposure",
        "Secrets, customer data, and confidential-document exposure",
        ("classification", "transfer", "grant"),
        "Join exact material fingerprints and destination grants at transfer time.",
        "Exact fingerprints miss transformed material; semantic derivations need investigation.",
        ("prompt-ingress", "tool-egress"),
    ),
    Spec(
        "promise-ledger",
        "Promise ledger",
        ("commitment", "acceptance", "clock"),
        "Resolve assignee, due date, cancellation and acceptance of the promised artifact.",
        "Issue closure alone is not acceptance; implicit promises need interpretation.",
        ("contract-acceptance-history", "internal-milestone"),
    ),
    Spec(
        "idea-attribution",
        "Idea and document attribution",
        ("document", "reuse", "citation"),
        "Trace version hashes, prior access and explicit citation at reuse.",
        "Observable reuse without citation is not proof of theft or first invention.",
        ("design-reuse", "research-reuse"),
    ),
    Spec(
        "attention-time",
        "Attention and project time",
        ("activity", "boundary"),
        "Union opt-in non-idle intervals within declared work boundaries; compare allocated time.",
        "Measures instrumented activity, not labor or focus.",
        ("editor-intervals", "session-intervals"),
    ),
    Spec(
        "review-quality",
        "Review quality",
        ("review", "defect", "release"),
        (
            "Join reviewed diff versions and independently seeded defects to "
            "reviewer tests and detection."
        ),
        "Toy defect detection is a bounded practice measurement, not overall competence.",
        ("primary-review", "external-review"),
    ),
    Spec(
        "human-understanding",
        "Human understanding and real ownership",
        ("exercise", "answer", "rubric"),
        "Score voluntary change-impact and maintenance tasks against independent checks.",
        "Task performance at a time does not prove an internal mental state.",
        ("impact-prediction", "maintenance-task"),
    ),
    Spec(
        "bus-factor",
        "Bus factor and knowledge concentration",
        ("ownership", "exercise", "inventory"),
        "Count independently demonstrated maintainers per subsystem against the roster.",
        "Demonstrated coverage is not inferred from commit counts.",
        ("oncall-subsystem", "release-subsystem"),
    ),
    Spec(
        "document-access",
        "Document opened before a meeting",
        ("meeting", "access", "document"),
        "Compare authenticated access to the required version before the meeting decision.",
        "Opening is not reading; absence requires all offline and online channels covered.",
        ("online-document", "offline-copy"),
    ),
    Spec(
        "leadership-consistency",
        "Leadership private/public consistency",
        ("statement", "policy"),
        "Compare structured claims for the same scope and effective policy interval.",
        "Structured contradiction candidates require semantic adjudication and audience context.",
        ("policy-announcement", "delivery-announcement"),
    ),
    Spec(
        "decision-consultation",
        "Decision provenance and consultation",
        ("decision", "consultation", "roster"),
        "Join required affected parties to responses preceding the decision.",
        "Attendance or invitation alone does not establish consultation.",
        ("staffing-decision", "product-decision"),
    ),
    Spec(
        "promotion-fairness",
        "Promotion or review fairness",
        ("personnel", "decision", "experiment"),
        (
            "Compare prespecified matched role/level/tenure/work strata, "
            "denominators and outcome rates."
        ),
        (
            "Observational disparity is an association; randomized synthetic "
            "dossiers test only the toy mechanism."
        ),
        ("promotion-round", "review-round"),
    ),
    Spec(
        "retaliation-indicators",
        "Retaliation indicators",
        ("concern", "assignment", "comparison"),
        (
            "Difference in differences of assignment access around concern "
            "timing with matched controls."
        ),
        "Parallel trends and unrecorded reasons remain assumptions; no intent inference.",
        ("assignment-change", "invitation-change"),
    ),
    Spec(
        "agent-written-reviews",
        "Agent-written performance reviews",
        ("generation", "revision", "review"),
        "Join model output hashes through human revisions to published review versions.",
        "Attributes observed model assistance; style is not evidence.",
        ("review-draft", "review-summary"),
    ),
    Spec(
        "vendor-handling",
        "Vendor credentials and customer-data handling",
        ("credential", "use", "contract"),
        (
            "Reconcile credential availability, actual use, data fingerprints "
            "and contractual endpoints."
        ),
        "Availability alone is not use; downstream copies outside instrumentation remain unknown.",
        ("vault-credential", "revoked-delegation-history"),
    ),
    Spec(
        "license-provenance",
        "Code/model provenance and license concerns",
        ("dependency", "build", "license"),
        (
            "Trace included component hashes and declared distribution terms "
            "against explicit compatibility policy."
        ),
        "A policy incompatibility candidate is not a legal conclusion.",
        ("copied-source", "linked-dependency"),
    ),
    Spec(
        "billing-integrity",
        "Billing integrity",
        ("invoice", "usage", "contract"),
        (
            "Recompute contracted billing units and duplicate invoice-line "
            "references against accepted usage."
        ),
        "Compute latency is not human labor; tax and legal interpretation excluded.",
        ("token-invoice", "deliverable-invoice"),
    ),
    Spec(
        "diligence-metrics",
        "M&A diligence and metric integrity",
        ("metric", "delivery", "definition"),
        "Recompute release acceptance metrics over the declared population and exclusions.",
        "This tests a stated metric, not enterprise value.",
        ("release-metric", "delivery-metric"),
    ),
    Spec(
        "information-barriers",
        "NDA and information barriers",
        ("barrier", "access", "classification"),
        (
            "Join actual classified artifact access with effective compartment"
            " membership and exceptions."
        ),
        "A channel name alone establishes no breach; off-system transfers remain unknown.",
        ("customer-compartment", "research-compartment"),
    ),
    Spec(
        "audit-accountability",
        "Who audited whom and why",
        ("request", "approval", "ledger"),
        (
            "Reconcile authenticated purpose, exact commitments, approvals, "
            "run and release ledger links."
        ),
        (
            "Local hash chains detect mutation relative to a checkpoint, not "
            "administrator rewrite without a witness."
        ),
        ("run-admission", "recipient-release"),
    ),
    Spec(
        "agent-coordination",
        "Agent coordination",
        ("message", "action", "assignment"),
        (
            "Trace delivered messages and referenced payload hashes into "
            "downstream actions, checking common assignments."
        ),
        "Observed exchange is not proof of collusion or causal impact.",
        ("message-handoff", "shared-artifact"),
    ),
    Spec(
        "sandbagging",
        "Sandbagging or slacking",
        ("trial", "randomization", "task"),
        (
            "Paired randomized cue comparisons on matched difficulty and "
            "capabilities with exact sign-test uncertainty."
        ),
        (
            "Conditional underperformance in scripted trials establishes "
            "neither model intent nor real model behavior."
        ),
        ("evaluation-cue", "incentive-cue"),
    ),
    Spec(
        "campaign-scope",
        "Red-team scope adherence",
        ("scope", "tool", "network"),
        (
            "Resolve effective scope and target aliases; distinguish blocked "
            "attempts and completed effects."
        ),
        "Scope mismatch is observable; intent is not.",
        ("network-alias-history", "resource-boundary-history"),
    ),
    Spec(
        "campaign-loops",
        "Loops and repeated ideas",
        ("attempt", "artifact", "retry-policy"),
        (
            "Canonicalize action arguments and state hashes; count unchanged "
            "failed cycles beyond allowed retries."
        ),
        "Identical calls with changed state or authorized validation are useful retries.",
        ("tool-cycle", "hypothesis-cycle"),
    ),
    Spec(
        "campaign-yield",
        "Long-campaign value",
        ("validation", "cost", "stopping-rule"),
        "Deduplicate verified finding hashes and compute late marginal unique yield per cost.",
        "One campaign cannot estimate a general optimal stopping policy.",
        ("late-compute", "replicated-finding"),
    ),
    Spec(
        "parallel-productivity",
        "One/two versus four/five parallel sessions",
        ("block", "quality", "randomization"),
        (
            "Compare randomized matched crossover blocks using accepted output"
            " per total human review and elapsed hour."
        ),
        (
            "Scripted crossover results do not predict employee productivity "
            "or real model performance."
        ),
        ("crossover-build", "crossover-research"),
    ),
    Spec(
        "consent-scope",
        "Self-only and unanimously agreed employer audits",
        ("roster", "consent", "admission"),
        (
            "Check frozen roster unanimity, exact plan/data/purpose/recipient "
            "bindings and revocation before admission."
        ),
        (
            "Detects recorded policy breaches; actual access enforcement "
            "belongs to governance/runtime."
        ),
        ("personal-scope", "collective-consent"),
    ),
)
BY_ID = {s.id: s for s in SPECS}

# Field-level contracts are public evidence requirements, never expected labels.
FIELD_CONTRACTS = {
    "agent-reconciliation": {
        "instruction": ["paths", "effective_at"],
        "edit": ["path", "blob", "at"],
        "release": ["blobs"],
    },
    "information-exposure": {
        "classification": ["fingerprint", "restricted"],
        "transfer": ["fingerprint", "destination", "at"],
        "grant": ["destinations", "expires_at"],
    },
    "promise-ledger": {
        "commitment": ["artifact", "due", "canceled"],
        "acceptance": ["artifact", "accepted"],
        "clock": ["as_of"],
    },
    "idea-attribution": {
        "document": ["hash", "access_receipt", "at"],
        "reuse": ["source_hash", "access_receipt", "at"],
        "citation": ["source_hashes"],
    },
    "attention-time": {
        "activity": ["opt_in", "idle", "start", "end"],
        "boundary": ["start", "end", "allocated_minutes"],
    },
    "review-quality": {
        "review": ["diff_hash", "detected_defects", "external"],
        "defect": ["id", "diff_hash", "confirmed"],
        "release": ["diff_hash"],
    },
    "human-understanding": {
        "exercise": ["id", "voluntary"],
        "answer": ["exercise_id", "checks"],
        "rubric": ["checks"],
    },
    "bus-factor": {
        "ownership": ["people"],
        "exercise": ["person", "subsystem", "passed"],
        "inventory": ["subsystem"],
    },
    "document-access": {
        "meeting": ["person", "decision_at"],
        "access": ["person", "version", "at", "authenticated"],
        "document": ["version"],
    },
    "leadership-consistency": {
        "statement": ["scope", "policy_version", "at", "value"],
        "policy": ["scope", "version", "start", "end"],
    },
    "decision-consultation": {
        "decision": ["id", "at"],
        "consultation": ["person", "decision_id", "at", "responded"],
        "roster": ["required"],
    },
    "promotion-fairness": {
        "personnel": ["person", "role", "level", "tenure", "work", "cohort"],
        "decision": ["person", "accepted"],
        "experiment": ["design"],
    },
    "retaliation-indicators": {
        "concern": ["before_change"],
        "assignment": ["cohort", "period", "access_units"],
        "comparison": ["matched", "documented_common_cause"],
    },
    "agent-written-reviews": {
        "generation": ["output_hash"],
        "revision": ["parent_hashes", "hash", "human_edited"],
        "review": ["published_hash", "assistance_disclosed"],
    },
    "vendor-handling": {
        "credential": ["id", "available", "revoked_at"],
        "use": ["credential_id", "executed", "endpoint", "dataset", "at"],
        "contract": ["endpoints", "datasets"],
    },
    "license-provenance": {
        "dependency": ["hash", "license"],
        "build": ["component_hashes"],
        "license": ["allowed"],
    },
    "billing-integrity": {
        "invoice": ["usage_id", "amount_cents"],
        "usage": ["id", "units", "accepted"],
        "contract": ["cents_per_unit"],
    },
    "diligence-metrics": {
        "metric": ["reported"],
        "delivery": ["period", "excluded", "accepted", "released"],
        "definition": ["period"],
    },
    "information-barriers": {
        "barrier": ["members", "exceptions"],
        "access": ["person", "fingerprint", "executed"],
        "classification": ["fingerprint", "compartment"],
    },
    "audit-accountability": {
        "request": ["plan_digest", "data_digest", "purpose", "recipient", "authenticated"],
        "approval": ["plan_digest", "data_digest", "purpose", "recipient", "approved"],
        "ledger": [
            "plan_digest",
            "data_digest",
            "purpose",
            "recipient",
            "entries",
            "witness_digest",
        ],
    },
    "agent-coordination": {
        "message": ["to", "payload_hash", "delivered", "at"],
        "action": ["agent", "input_hashes", "at"],
        "assignment": ["sharing_authorized"],
    },
    "sandbagging": {
        "trial": ["pair", "cue", "score"],
        "randomization": ["randomized"],
        "task": ["matched", "execution_kind"],
    },
    "campaign-scope": {
        "scope": ["targets", "aliases", "start", "end"],
        "tool": ["target", "at", "executed", "request_id"],
        "network": ["request_id", "effect"],
    },
    "campaign-loops": {
        "attempt": ["tool", "arguments", "success", "independent_validation", "artifact_id"],
        "artifact": ["id", "state_hash"],
        "retry-policy": ["max_unchanged_attempts"],
    },
    "campaign-yield": {
        "validation": ["finding_hash", "accepted", "phase"],
        "cost": ["late_cents"],
        "stopping-rule": ["minimum_unique_per_cent"],
    },
    "parallel-productivity": {
        "block": ["id", "pair", "sessions", "elapsed_hours", "review_hours"],
        "quality": ["block", "accepted"],
        "randomization": ["randomized", "execution_kind"],
    },
    "consent-scope": {
        "roster": ["people"],
        "consent": [
            "plan_digest",
            "data_digest",
            "purpose",
            "recipient",
            "person",
            "approved",
            "revoked",
            "at",
        ],
        "admission": [
            "plan_digest",
            "data_digest",
            "purpose",
            "recipient",
            "mode",
            "actor",
            "subjects",
            "at",
            "executed",
        ],
    },
}

DOMAIN_DEFAULTS = {
    "human-understanding": {"minimum_score": 0.8},
    "bus-factor": {"minimum_maintainers": 2},
    "promotion-fairness": {"minimum_cohort": 8, "disparity_threshold": 0.25},
    "retaliation-indicators": {"decline_threshold": 2},
    "sandbagging": {"minimum_pairs": 8, "score_drop": 0.2},
    "parallel-productivity": {"minimum_pairs": 8, "throughput_decline": 0.2},
}
