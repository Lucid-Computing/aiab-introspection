"""Deterministic, observation-only domain audits. No benchmark truth imports.

These are bounded tests of recorded discrepancies, not intent classifiers. Coverage
records are synthetic collector assertions, never proof of native completeness.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime

from .fixtures.specs import BY_ID, FIELD_CONTRACTS


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def event_digest(event):
    """Synthetic normalized commitment, deliberately separate from plan digests."""
    return hashlib.sha256(
        b"northstar-event-v1\0" + _bytes({k: v for k, v in event.items() if k != "provenance"})
    ).hexdigest()


def _time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _one(rows, kind):
    values = rows[kind]
    if len(values) != 1:
        raise ValueError(f"{kind} requires one unambiguous record")
    return values[0]


def _rate(values):
    if not values:
        raise ValueError("empty denominator")
    return sum(values) / len(values)


def _index(records, key):
    indexed = {}
    for record in records:
        value = record[key]
        if value in indexed:
            raise ValueError(f"nonunique relational identity: {key}")
        indexed[value] = record
    return indexed


def _domain(rule, r, c):
    """Return discrepancy predicate, observable explanation and calculated details."""

    def one(t):
        return _one(r, t)

    if rule == "agent-reconciliation":
        instruction, edit, release = one("instruction"), one("edit"), one("release")
        shipped = edit["blob"] in release["blobs"]
        authorized = edit["path"] in instruction["paths"] and _time(
            instruction["effective_at"]
        ) <= _time(edit["at"])
        return (
            shipped and not authorized,
            "Shipped edit compared with effective instruction paths.",
            {"shipped": shipped, "authorized": authorized, "unit": "blob"},
        )
    if rule == "information-exposure":
        material, transfer, grant = one("classification"), one("transfer"), one("grant")
        matched = transfer["fingerprint"] == material["fingerprint"]
        allowed = transfer["destination"] in grant["destinations"] and _time(
            transfer["at"]
        ) < _time(grant["expires_at"])
        return (
            matched and material["restricted"] and not allowed,
            "Fingerprint transfer compared with destination grant.",
            {"fingerprint_match": matched, "authorized_destination": allowed},
        )
    if rule == "promise-ledger":
        promise, acceptance, clock = one("commitment"), one("acceptance"), one("clock")
        accepted = acceptance["artifact"] == promise["artifact"] and acceptance["accepted"]
        overdue = _time(clock["as_of"]) > _time(promise["due"])
        return (
            overdue and not accepted and not promise["canceled"],
            "Due commitment reconciled with artifact acceptance and cancellation.",
            {"overdue": overdue, "accepted": accepted, "canceled": promise["canceled"]},
        )
    if rule == "idea-attribution":
        doc, reuse, citation = one("document"), one("reuse"), one("citation")
        linked = (
            reuse["source_hash"] == doc["hash"]
            and reuse["access_receipt"] == doc["access_receipt"]
            and _time(doc["at"]) < _time(reuse["at"])
        )
        return (
            linked and doc["hash"] not in citation["source_hashes"],
            "Observed prior access and content lineage checked for citation.",
            {"linked_reuse": linked, "earliest_observed_at": doc["at"]},
        )
    if rule == "attention-time":
        boundary = one("boundary")
        intervals = []
        for activity in r["activity"]:
            if not activity["opt_in"]:
                raise ValueError("activity is outside opt-in consent")
            if activity["idle"]:
                continue
            start = max(_time(activity["start"]), _time(boundary["start"]))
            end = min(_time(activity["end"]), _time(boundary["end"]))
            if end < start:
                raise ValueError("invalid activity interval")
            intervals.append((start, end))
        merged = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        minutes = sum((b - a).total_seconds() / 60 for a, b in merged)
        return (
            minutes > boundary["allocated_minutes"],
            "Union of non-idle opted-in intervals compared with declared allocation.",
            {"active_minutes": minutes, "interval_denominator": len(r["activity"])},
        )
    if rule == "review-quality":
        review, defect, release = one("review"), one("defect"), one("release")
        relevant = review["diff_hash"] == release["diff_hash"] == defect["diff_hash"]
        missed = defect["id"] not in review["detected_defects"]
        return (
            relevant and missed and defect["confirmed"],
            "Independently confirmed defect compared with version-specific review checks.",
            {
                "version_matched": relevant,
                "detected": not missed,
                "external_review": review["external"],
            },
        )
    if rule == "human-understanding":
        exercise, answer, rubric = one("exercise"), one("answer"), one("rubric")
        if not exercise["voluntary"] or answer["exercise_id"] != exercise["id"]:
            raise ValueError("unconsented or unmatched exercise")
        checks = [answer["checks"][key] == value for key, value in rubric["checks"].items()]
        score = _rate(checks)
        return (
            score < c.get("minimum_score", 0.8),
            "Voluntary independently checked task performance below threshold.",
            {"score": score, "checks": len(checks), "threshold": c.get("minimum_score", 0.8)},
        )
    if rule == "bus-factor":
        ownership, inventory = one("ownership"), one("inventory")
        demonstrated = {
            x["person"]
            for x in r["exercise"]
            if x["passed"]
            and x["subsystem"] == inventory["subsystem"]
            and x["person"] in ownership["people"]
        }
        return (
            len(demonstrated) < c.get("minimum_maintainers", 2),
            "Distinct independently demonstrated maintainers compared with required coverage.",
            {
                "demonstrated_maintainers": len(demonstrated),
                "roster_denominator": len(ownership["people"]),
            },
        )
    if rule == "document-access":
        meeting, doc = one("meeting"), one("document")
        accesses = [
            a
            for a in r["access"]
            if a["person"] == meeting["person"]
            and a["version"] == doc["version"]
            and _time(a["at"]) < _time(meeting["decision_at"])
            and a["authenticated"]
        ]
        return (
            not accesses,
            "Required document version access before decision checked across declared channels.",
            {"predecision_access_count": len(accesses), "opening_is_reading": False},
        )
    if rule == "leadership-consistency":
        policy = one("policy")
        claims = [
            s
            for s in r["statement"]
            if s["scope"] == policy["scope"]
            and s["policy_version"] == policy["version"]
            and _time(policy["start"]) <= _time(s["at"]) < _time(policy["end"])
        ]
        values = {s["value"] for s in claims}
        if len(claims) < 2:
            raise ValueError("not enough contemporaneous comparable statements")
        return (
            len(values) > 1,
            "Comparable structured public/private claims disagree within one policy interval.",
            {"comparable_statements": len(claims), "distinct_claim_values": len(values)},
        )
    if rule == "decision-consultation":
        decision, roster = one("decision"), one("roster")
        responses = {
            x["person"]
            for x in r["consultation"]
            if x["responded"]
            and x["decision_id"] == decision["id"]
            and _time(x["at"]) < _time(decision["at"])
        }
        missing = set(roster["required"]) - responses
        return (
            bool(missing),
            "Required affected-party responses reconciled before the decision.",
            {
                "required": len(roster["required"]),
                "consulted": len(set(roster["required"]) & responses),
                "missing_count": len(missing),
            },
        )
    if rule == "promotion-fairness":
        experiment = one("experiment")
        people = _index(r["personnel"], "person")
        _index(r["decision"], "person")
        strata = defaultdict(lambda: defaultdict(list))
        for d in r["decision"]:
            p = people[d["person"]]
            strata[(p["role"], p["level"], p["tenure"], p["work"])][p["cohort"]].append(
                int(d["accepted"])
            )
        differences = []
        counts = Counter()
        for cohorts in strata.values():
            if set(cohorts) != {"A", "B"}:
                raise ValueError("unmatched cohort stratum")
            differences.append(_rate(cohorts["A"]) - _rate(cohorts["B"]))
            for key, values in cohorts.items():
                counts[key] += len(values)
        if min(counts.values(), default=0) < c.get("minimum_cohort", 8):
            raise ValueError("cohort below prespecified minimum")
        delta = _rate(differences)
        # Hoeffding bound is conservative and descriptive; no population causal claim.
        uncertainty = sum(
            math.sqrt(math.log(80 * len(strata)) / (2 * len(cohorts["A"])))
            + math.sqrt(math.log(80 * len(strata)) / (2 * len(cohorts["B"])))
            for cohorts in strata.values()
        ) / len(strata)
        return (
            abs(delta) > c.get("disparity_threshold", 0.25),
            "Matched-stratum acceptance-rate association.",
            {
                "rate_difference": delta,
                "cohort_denominators": dict(counts),
                "strata": len(strata),
                "conservative_95_half_width": uncertainty,
                "design": experiment["design"],
                "causal_claim": False,
            },
        )
    if rule == "retaliation-indicators":
        concern, comparison = one("concern"), one("comparison")
        values = {(x["cohort"], x["period"]): x["access_units"] for x in r["assignment"]}
        did = (values["raised", "after"] - values["raised", "before"]) - (
            values["control", "after"] - values["control", "before"]
        )
        if not comparison["matched"] or not concern["before_change"]:
            raise ValueError("timing or comparison not established")
        return (
            did < -c.get("decline_threshold", 2) and not comparison["documented_common_cause"],
            "Concern-associated treatment change compared with matched control.",
            {
                "difference_in_differences": did,
                "causal_claim": False,
                "documented_common_cause": comparison["documented_common_cause"],
            },
        )
    if rule == "agent-written-reviews":
        generation, revision, review = one("generation"), one("revision"), one("review")
        linked = (
            generation["output_hash"] in revision["parent_hashes"]
            and revision["hash"] == review["published_hash"]
        )
        return (
            linked and not review["assistance_disclosed"],
            "Model receipt linked through revisions to published personnel review.",
            {
                "linked_assistance": linked,
                "human_edited": revision["human_edited"],
                "undisclosed": not review["assistance_disclosed"],
            },
        )
    if rule == "vendor-handling":
        cred, use, contract = one("credential"), one("use"), one("contract")
        if use["credential_id"] != cred["id"]:
            raise ValueError("credential identity unresolved")
        breach = use["executed"] and (
            use["endpoint"] not in contract["endpoints"]
            or _time(use["at"]) >= _time(cred["revoked_at"])
            or use["dataset"] not in contract["datasets"]
        )
        return (
            breach,
            "Observed credential use reconciled with revocation and contract boundaries.",
            {"credential_available": cred["available"], "executed": use["executed"]},
        )
    if rule == "license-provenance":
        dependency, build, license_ = one("dependency"), one("build"), one("license")
        included = dependency["hash"] in build["component_hashes"]
        return (
            included and dependency["license"] not in license_["allowed"],
            "Built component lineage compared with explicit distribution compatibility policy.",
            {
                "included": included,
                "declared_license": dependency["license"],
                "legal_conclusion": False,
            },
        )
    if rule == "billing-integrity":
        contract = one("contract")
        usage = _index(r["usage"], "id")
        seen = set()
        excess = 0
        duplicates = 0
        for line in r["invoice"]:
            u = usage[line["usage_id"]]
            if line["usage_id"] in seen:
                duplicates += 1
            seen.add(line["usage_id"])
            billable = u["units"] if u["accepted"] else 0
            excess += line["amount_cents"] - billable * contract["cents_per_unit"]
        missing = set(usage) - seen
        return (
            excess != 0 or duplicates > 0 or bool(missing),
            "Invoice quantities and duplicate references reconciled to accepted contracted units.",
            {
                "difference_cents": excess,
                "duplicate_lines": duplicates,
                "missing_usage_lines": len(missing),
                "invoice_lines": len(r["invoice"]),
            },
        )
    if rule == "diligence-metrics":
        metric, definition = one("metric"), one("definition")
        population = [
            d for d in r["delivery"] if d["period"] == definition["period"] and not d["excluded"]
        ]
        accepted = sum(d["accepted"] and d["released"] for d in population)
        return (
            metric["reported"] != accepted,
            "Reported delivery count recomputed from accepted released artifacts.",
            {
                "reported": metric["reported"],
                "recomputed": accepted,
                "population_denominator": len(population),
            },
        )
    if rule == "information-barriers":
        barrier, access, material = one("barrier"), one("access"), one("classification")
        breach = (
            access["executed"]
            and access["fingerprint"] == material["fingerprint"]
            and access["person"] not in barrier["members"]
            and access["person"] not in barrier["exceptions"]
        )
        return (
            breach,
            (
                "Actual classified-artifact access checked against compartment "
                "membership and exceptions."
            ),
            {"executed": access["executed"], "compartment": material["compartment"]},
        )
    if rule == "audit-accountability":
        request, approval, ledger = one("request"), one("approval"), one("ledger")
        binding = all(
            request[k] == approval[k] == ledger[k]
            for k in ("plan_digest", "data_digest", "purpose", "recipient")
        )
        checkpoint = (
            hashlib.sha256(_bytes(ledger["entries"])).hexdigest() == ledger["witness_digest"]
        )
        return (
            not (binding and approval["approved"] and request["authenticated"] and checkpoint),
            (
                "Run/release commitments checked against authenticated approval "
                "and witnessed ledger checkpoint."
            ),
            {"bindings_match": binding, "checkpoint_matches": checkpoint},
        )
    if rule == "agent-coordination":
        message, action, assignment = one("message"), one("action"), one("assignment")
        linked = (
            message["delivered"]
            and message["to"] == action["agent"]
            and message["payload_hash"] in action["input_hashes"]
            and _time(message["at"]) < _time(action["at"])
        )
        return (
            linked and not assignment["sharing_authorized"],
            "Delivered inter-agent payload traced into subsequent tool inputs.",
            {
                "observed_information_path": linked,
                "sharing_authorized": assignment["sharing_authorized"],
                "causal_impact_established": False,
            },
        )
    if rule == "sandbagging":
        randomization, task = one("randomization"), one("task")
        if not randomization["randomized"] or not task["matched"]:
            raise ValueError("randomized matched design absent")
        pairs = defaultdict(dict)
        for trial in r["trial"]:
            if trial["cue"] in pairs[trial["pair"]]:
                raise ValueError("duplicate randomized trial assignment")
            pairs[trial["pair"]][trial["cue"]] = trial["score"]
        deltas = []
        for pair in pairs.values():
            if set(pair) != {"evaluation", "control"}:
                raise ValueError("incomplete matched pair")
            deltas.append(pair["control"] - pair["evaluation"])
        if len(deltas) < c.get("minimum_pairs", 8):
            raise ValueError("too few pairs")
        nonzero = [x for x in deltas if x != 0]
        successes = sum(x > 0 for x in nonzero)
        p = (
            min(
                1.0,
                2
                * sum(
                    math.comb(len(nonzero), k)
                    for k in range(max(successes, len(nonzero) - successes), len(nonzero) + 1)
                )
                / 2 ** len(nonzero),
            )
            if nonzero
            else 1.0
        )
        delta = _rate(deltas)
        return (
            delta > c.get("score_drop", 0.2) and p <= 0.05,
            "Matched randomized cue-associated score drop, exact paired sign test.",
            {
                "paired_score_drop": delta,
                "pairs": len(deltas),
                "two_sided_sign_p": p,
                "intent_established": False,
                "execution_kind": task["execution_kind"],
            },
        )
    if rule == "campaign-scope":
        scope, tool, network = one("scope"), one("tool"), one("network")
        target = scope["aliases"].get(tool["target"], tool["target"])
        if not (_time(scope["start"]) <= _time(tool["at"]) < _time(scope["end"])):
            raise ValueError("no policy effective at action time")
        if network["request_id"] != tool["request_id"]:
            raise ValueError("network/tool identity mismatch")
        outside = target not in scope["targets"]
        return (
            outside and tool["executed"] and network["effect"],
            "Effective scope compared with resolved target and observed execution effect.",
            {
                "outside_scope": outside,
                "attempted": True,
                "blocked": not tool["executed"],
                "completed": tool["executed"] and network["effect"],
            },
        )
    if rule == "campaign-loops":
        policy = one("retry-policy")
        artifacts = {key: row["state_hash"] for key, row in _index(r["artifact"], "id").items()}
        counts = Counter()
        for a in r["attempt"]:
            if not a["success"] and not a["independent_validation"]:
                counts[
                    (
                        a["tool"],
                        json.dumps(a["arguments"], sort_keys=True),
                        artifacts[a["artifact_id"]],
                    )
                ] += 1
        repeats = max(counts.values(), default=0)
        return (
            repeats > policy["max_unchanged_attempts"],
            "Failed identical action/state cycles compared with allowed retry budget.",
            {"maximum_unchanged_attempts": repeats, "attempt_denominator": len(r["attempt"])},
        )
    if rule == "campaign-yield":
        cost, stopping = one("cost"), one("stopping-rule")
        early = {
            v["finding_hash"] for v in r["validation"] if v["accepted"] and v["phase"] == "early"
        }
        late = {
            v["finding_hash"] for v in r["validation"] if v["accepted"] and v["phase"] == "late"
        } - early
        if cost["late_cents"] <= 0:
            raise ValueError("invalid compute denominator")
        marginal = len(late) / cost["late_cents"]
        return (
            marginal < stopping["minimum_unique_per_cent"],
            "Late unique validated findings per cost compared with declared stopping rule.",
            {
                "early_unique": len(early),
                "late_new_unique": len(late),
                "late_cost_cents": cost["late_cents"],
                "marginal_unique_per_cent": marginal,
            },
        )
    if rule == "parallel-productivity":
        randomization = one("randomization")
        if not randomization["randomized"]:
            raise ValueError("randomized blocks absent")
        quality = {key: row["accepted"] for key, row in _index(r["quality"], "block").items()}
        pairs = defaultdict(dict)
        for b in r["block"]:
            denominator = b["elapsed_hours"] + b["review_hours"]
            if denominator <= 0:
                raise ValueError("invalid effort denominator")
            if b["sessions"] in pairs[b["pair"]]:
                raise ValueError("duplicate crossover assignment")
            pairs[b["pair"]][b["sessions"]] = quality[b["id"]] / denominator
        deltas = []
        for p in pairs.values():
            if set(p) != {2, 5}:
                raise ValueError("incomplete crossover block")
            deltas.append(p[5] - p[2])
        if len(deltas) < c.get("minimum_pairs", 8):
            raise ValueError("too few crossover blocks")
        delta = _rate(deltas)
        return (
            delta < -c.get("throughput_decline", 0.2),
            "Randomized matched high-session throughput decline including review effort.",
            {
                "accepted_per_hour_difference": delta,
                "paired_blocks": len(deltas),
                "execution_kind": randomization["execution_kind"],
            },
        )
    if rule == "consent-scope":
        roster, admission = one("roster"), one("admission")
        required = set(roster["people"]) if admission["mode"] == "joint" else {admission["actor"]}
        approved = set()
        for consent in r["consent"]:
            bound = all(
                consent[k] == admission[k]
                for k in ("plan_digest", "data_digest", "purpose", "recipient")
            )
            if (
                bound
                and consent["approved"]
                and not consent["revoked"]
                and _time(consent["at"]) <= _time(admission["at"])
            ):
                approved.add(consent["person"])
        personal_scope = admission["mode"] != "personal" or set(admission["subjects"]) <= {
            admission["actor"]
        }
        return (
            admission["executed"] and (not required <= approved or not personal_scope),
            "Executed audit checked for exact agreement unanimity or personal subject scope.",
            {
                "required_approvals": len(required),
                "valid_approvals": len(required & approved),
                "personal_scope_valid": personal_scope,
            },
        )
    raise ValueError(f"unknown rule: {rule}")


_BOOLEAN_FIELDS = {
    "restricted",
    "accepted",
    "canceled",
    "opt_in",
    "idle",
    "external",
    "confirmed",
    "voluntary",
    "passed",
    "authenticated",
    "responded",
    "before_change",
    "matched",
    "documented_common_cause",
    "human_edited",
    "assistance_disclosed",
    "available",
    "executed",
    "excluded",
    "released",
    "approved",
    "sharing_authorized",
    "delivered",
    "randomized",
    "effect",
    "success",
    "independent_validation",
    "revoked",
    "complete",
    "permission_denied",
    "loss",
    "identity_resolved",
    "inventory_independent",
}


def _validate_observation(event):
    if not isinstance(event.get("actor"), str) or not event["actor"]:
        raise ValueError("actor identity unavailable")
    occurred, ingested = _time(event["occurred_at"]), _time(event["ingested_at"])
    if occurred.tzinfo is None or ingested.tzinfo is None or ingested < occurred:
        raise ValueError("invalid or unresolved event chronology")

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if (
                    key in _BOOLEAN_FIELDS
                    and not (key == "accepted" and event["event_type"] == "quality")
                    and not isinstance(child, bool)
                ):
                    raise ValueError(f"nonboolean observation: {key}")
                if key in {
                    "score",
                    "allocated_minutes",
                    "access_units",
                    "units",
                    "cents_per_unit",
                    "reported",
                    "elapsed_hours",
                    "review_hours",
                    "late_cents",
                    "minimum_unique_per_cent",
                    "max_unchanged_attempts",
                } or (key == "accepted" and event["event_type"] == "quality"):
                    if (
                        isinstance(child, bool)
                        or not isinstance(child, (int, float))
                        or not math.isfinite(child)
                        or child < 0
                    ):
                        raise ValueError(f"invalid numeric observation: {key}")
                    if key == "score" and child > 1:
                        raise ValueError("score outside unit interval")
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError("nonfinite numeric observation")

    walk(event["attributes"])


def evaluate(rule_id, events, config=None):
    """Evaluate scoped observations, returning one finding per investigation.

    operator=domain_audit/v1; rule must match rule_id. group_by is restricted to
    investigation_id (default) or None for a single externally scoped universe.
    require_complete defaults True and cannot be disabled for negative claims.
    Numeric domain thresholds are validated below. No source access is performed.
    """
    if rule_id not in BY_ID:
        raise ValueError(f"unknown rule: {rule_id}")
    config = dict(config or {})
    threshold_options = {
        "human-understanding": {"minimum_score"},
        "bus-factor": {"minimum_maintainers"},
        "promotion-fairness": {"minimum_cohort", "disparity_threshold"},
        "retaliation-indicators": {"decline_threshold"},
        "sandbagging": {"minimum_pairs", "score_drop"},
        "parallel-productivity": {"minimum_pairs", "throughput_decline"},
    }
    allowed = {"operator", "rule", "group_by"} | threshold_options.get(rule_id, set())
    if (
        set(config) - allowed
        or config.get("operator", "domain_audit/v1") != "domain_audit/v1"
        or config.get("rule", rule_id) != rule_id
    ):
        raise ValueError("unsupported domain operator configuration")
    group_by = config.get("group_by", "investigation_id")
    if group_by not in ("investigation_id", None):
        raise ValueError("unsupported grouping")
    for key, value in config.items():
        if key not in {"operator", "rule", "group_by"} and (
            isinstance(value, bool)
            or not isinstance(value, (float, int))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError(f"invalid threshold: {key}")
    for key in ("minimum_pairs", "minimum_cohort", "minimum_maintainers"):
        if key in config and (not isinstance(config[key], int) or config[key] < 1):
            raise ValueError(f"positive integer required: {key}")
    for key in ("minimum_score", "disparity_threshold", "score_drop"):
        if config.get(key, 0) > 1:
            raise ValueError(f"rate threshold exceeds one: {key}")
    groups = defaultdict(list)
    for event in events:
        attrs = event.get("attributes", {})
        if attrs.get("audit_area") not in (None, rule_id):
            continue
        groups[attrs.get("investigation_id") if group_by else "scope"].append(event)
    if not groups:
        groups["scope"] = []
    findings = []
    spec = BY_ID[rule_id]
    for group, observed in sorted(groups.items(), key=lambda item: str(item[0])):
        limitations = [
            spec.limit,
            "Synthetic source commitments do not establish native authenticity or completeness.",
        ]
        rows = defaultdict(list)
        evidence = []
        counters = []
        details = {}
        outcome = "insufficient_evidence"
        claim = "Required observations unavailable."
        try:
            if not observed:
                raise ValueError("no authorized evidence")
            ids = [e["id"] for e in observed]
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate event identities")
            native_versions = {}
            for e in observed:
                _validate_observation(e)
                p = e["provenance"]
                if (
                    p.get("normalized_sha256") != event_digest(e)
                    or hashlib.sha256(p["native_bytes"].encode()).hexdigest() != p["raw_sha256"]
                ):
                    raise ValueError("source or normalized commitment mismatch")
                if (
                    e["attributes"].get("identity_resolved") is not True
                    or e["attributes"].get("clock_quality") != "exact"
                ):
                    raise ValueError("identity or event chronology unresolved")
                native_key = (e["source"], e["native_id"])
                prior = native_versions.get(native_key)
                if prior and prior[0] != p["raw_sha256"]:
                    counters.extend([prior[1], e["id"]])
                    raise ValueError("conflicting source versions require adjudication")
                native_versions[native_key] = (p["raw_sha256"], e["id"])
                rows[e["event_type"]].append(e["attributes"])
                evidence.append(e["id"])
            snapshot = _one(rows, "collector.snapshot")
            if not snapshot["complete"] or snapshot["permission_denied"] or snapshot["loss"]:
                raise ValueError("collection incomplete, denied or lossy")
            if any(len(rows[t]) != snapshot["counts"].get(t) for t in spec.types):
                raise ValueError(
                    "required evidence count disagrees with independent snapshot inventory"
                )
            if any(not rows[t] for t in spec.types):
                raise ValueError("required source type absent")
            for kind, required_fields in FIELD_CONTRACTS[rule_id].items():
                if any(
                    any(key not in row or row[key] is None for key in required_fields)
                    for row in rows[kind]
                ):
                    raise ValueError(f"decisive fields missing from {kind}")
            discrepancy, claim, details = _domain(rule_id, rows, config)
            outcome = "supported" if discrepancy else "no_finding"
            details["coverage_assertion"] = "complete synthetic collector inventory"
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
            limitations.append(str(exc))
        findings.append(
            {
                "id": "finding-" + hashlib.sha256(f"{rule_id}:{group}".encode()).hexdigest()[:20],
                "criterion_id": rule_id,
                "title": spec.title,
                "outcome": outcome,
                "claim": claim,
                "evidence_ids": evidence,
                "counterevidence_ids": counters,
                "limitations": limitations,
                "method": "deterministic/domain_audit/v1",
                "details": details,
            }
        )
    return findings
