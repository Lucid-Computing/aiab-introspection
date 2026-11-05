"""Offline assurance CLI. Hidden truth is opened only by the scoring process.

python -m aiab.workbench.fixtures.evaluation corpus --output /tmp/ns
python -m aiab.workbench.fixtures.evaluation run \
    --dataset /tmp/ns/observed/dataset.json --output /tmp/predictions.json
python -m aiab.workbench.fixtures.evaluation score \
    --truth /tmp/ns/private/truth.json \
    --predictions /tmp/predictions.json --output /tmp/score.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import defaultdict
from pathlib import Path

from ..analyzers import evaluate
from .generator import build, verify_partitions
from .specs import SPECS


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def immutable_write(path, value):
    """Exclusive creation, read-only artifact, embedded digest; never overwrite."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = _canonical(value)
    envelope = {"sha256": hashlib.sha256(body).hexdigest(), "payload": value}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(fd, "wb") as stream:
        stream.write(_canonical(envelope) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    return envelope["sha256"]


def read_artifact(path):
    envelope = json.loads(Path(path).read_text())
    if hashlib.sha256(_canonical(envelope["payload"])).hexdigest() != envelope["sha256"]:
        raise ValueError("artifact digest mismatch")
    return envelope["payload"]


def create_corpus(output, seed=0, replicas=1):
    root = Path(output)
    root.mkdir(parents=True, exist_ok=False)
    (root / "private").mkdir(mode=0o700)
    dataset, truth = build(seed, replicas=replicas, include_truth=True)
    verify_partitions(dataset)
    observed_digest = immutable_write(root / "observed" / "dataset.json", dataset)
    truth["observations_sha256"] = observed_digest
    immutable_write(root / "private" / "truth.json", truth)
    return {
        "cases": len(truth["cases"]),
        "events": len(dataset["events"]),
        "observations_sha256": observed_digest,
    }


def predict(dataset):
    verify_partitions(dataset)
    # This function cannot access any truth path. Only scoped observations enter evaluate.
    findings = []
    for spec in SPECS:
        findings.extend(
            evaluate(spec.id, dataset["events"], {"operator": "domain_audit/v1", "rule": spec.id})
        )
    return {
        "version": "northstar-predictions/v1",
        "execution": "deterministic-only; no model calls",
        "observations_sha256": hashlib.sha256(_canonical(dataset)).hexdigest(),
        "findings": findings,
    }


def _interval(successes, total):
    if not total:
        return None
    p, z = successes / total, 1.959963984540054
    center = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return [max(0.0, center - half), min(1.0, center + half)]


def score(truth, predictions):
    if truth["observations_sha256"] != predictions["observations_sha256"]:
        raise ValueError("predictions are for different committed observations")
    predicted = {}
    for f in predictions["findings"]:
        if f["id"] in predicted:
            raise ValueError("duplicate prediction")
        if f["outcome"] not in ("supported", "contradicted", "no_finding", "insufficient_evidence"):
            raise ValueError("invalid outcome")
        predicted[f["id"]] = f
    families = defaultdict(list)
    seen = set()
    for case in truth["cases"]:
        key = (
            "finding-"
            + hashlib.sha256(f"{case['audit_id']}:{case['investigation_id']}".encode()).hexdigest()[
                :20
            ]
        )
        seen.add(key)
        finding = predicted.get(key)
        if finding and finding["criterion_id"] != case["audit_id"]:
            raise ValueError("criterion does not match case")
        families[(case["audit_id"], case["family"])].append((case, finding))
    if set(predicted) - seen:
        raise ValueError("predictions include unknown cases")
    reports = []
    for (audit, family), entries in sorted(families.items()):
        tp = fp = fn = tn = abstentions = correct = missing = citation_errors = 0
        expected_positive = expected_negative = expected_abstention = 0
        for case, finding in entries:
            expected = case["expected_supportable"]
            outcome = finding["outcome"] if finding else "missing_prediction"
            expected_positive += expected == "supported"
            expected_negative += expected == "no_finding"
            expected_abstention += expected == "insufficient_evidence"
            tp += outcome == "supported" and expected == "supported"
            fp += outcome == "supported" and expected != "supported"
            fn += expected == "supported" and outcome != "supported"
            tn += expected == "no_finding" and outcome in ("no_finding", "contradicted")
            abstentions += outcome == "insufficient_evidence"
            correct += outcome == expected
            missing += finding is None
            if finding:
                cited = finding["evidence_ids"] + finding["counterevidence_ids"]
                citation_errors += len(set(cited) - set(case["evidence_ids"]))
        reports.append(
            {
                "audit_id": audit,
                "family": family,
                "cases": len(entries),
                "expected_positive": expected_positive,
                "expected_negative": expected_negative,
                "expected_abstention": expected_abstention,
                "tp": tp,
                "fp": fp,
                "fn_including_abstention": fn,
                "tn": tn,
                "precision": tp / (tp + fp) if tp + fp else None,
                "precision_denominator": tp + fp,
                "precision_95_wilson": _interval(tp, tp + fp),
                "recall": tp / expected_positive if expected_positive else None,
                "recall_denominator": expected_positive,
                "recall_95_wilson": _interval(tp, expected_positive),
                "abstentions": abstentions,
                "abstention_denominator": len(entries),
                "exact_supportable_matches": correct,
                "errors": len(entries) - correct,
                "missing_predictions": missing,
                "citation_errors": citation_errors,
            }
        )
    return {
        "version": "northstar-score/v1",
        "families": reports,
        "cases": len(truth["cases"]),
        "limitations": [
            "Template-based synthetic development coverage, not held-out real-world accuracy.",
            (
                "Intervals use case-level binomial assumptions; correlated "
                "variants limit interpretation."
            ),
            "No independent annotation or real DeepSeek execution is included.",
            (
                "World violations with unobservable evidence are scored on "
                "supportable outcome, not hidden mechanism."
            ),
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    corpus = sub.add_parser("corpus")
    corpus.add_argument("--output", required=True)
    corpus.add_argument("--seed", type=int, default=0)
    corpus.add_argument(
        "--replicas",
        type=int,
        default=1,
        help=(
            "432 cases and about 3248 events per replica; 31 ~100k, 308 ~1m "
            "events. Memory grows with corpus."
        ),
    )
    run = sub.add_parser("run")
    run.add_argument("--dataset", required=True)
    run.add_argument("--output", required=True)
    scoring = sub.add_parser("score")
    scoring.add_argument("--truth", required=True)
    scoring.add_argument("--predictions", required=True)
    scoring.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "corpus":
        print(json.dumps(create_corpus(args.output, args.seed, args.replicas)))
    elif args.command == "run":
        result = predict(read_artifact(args.dataset))
        print(immutable_write(args.output, result))
    else:
        result = score(read_artifact(args.truth), read_artifact(args.predictions))
        print(immutable_write(args.output, result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
