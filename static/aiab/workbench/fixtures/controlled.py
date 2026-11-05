"""Actual local Git/build/tool executions with scripted actors, never model runs.

Run: python -m aiab.workbench.fixtures.controlled --output /tmp/northstar-executed
All effects are confined to the newly created output tree. Artifacts include real
Git commits/diffs, build/defect test exit codes and a mock-service campaign trace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from .evaluation import immutable_write
from .generator import seal


def execute(output, seed=0, task_families=12, seeds=5):
    if task_families < 1 or seeds < 1:
        raise ValueError("positive task and seed counts required")
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=False)
    repo = root / "repository"
    repo.mkdir()
    executions = []

    def command(args, *, check=True):
        env = dict(os.environ)
        env.update(
            {
                "GIT_AUTHOR_DATE": "2026-10-01T12:00:00+00:00",
                "GIT_COMMITTER_DATE": "2026-10-01T12:00:00+00:00",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
            }
        )
        proc = subprocess.run(args, cwd=repo, env=env, capture_output=True, text=True, timeout=30)
        record = {
            "argv": args,
            "cwd": "repository",
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "execution_kind": "actual_local_scripted_tool",
            "at": datetime.now(UTC).isoformat(),
        }
        executions.append(record)
        if check and proc.returncode:
            raise RuntimeError(record)
        return proc.stdout.strip()

    command(["git", "init", "-b", "main"])
    command(["git", "config", "user.name", "Northstar fixture actor"])
    command(["git", "config", "user.email", "fixture@northstar.invalid"])
    command(["git", "config", "commit.gpgsign", "false"])
    command(["git", "config", "core.hooksPath", os.devnull])
    (repo / "calculator.py").write_text(
        "def average(values):\n    return sum(values) / len(values)\n"
    )
    (repo / "test_calculator.py").write_text(
        "from calculator import average\nassert average([2, 4]) == 3\nassert average([]) == 0\n"
    )
    (repo / "requirements.json").write_text(
        json.dumps(
            {
                "authorized_paths": ["calculator.py"],
                "acceptance": "empty list returns zero",
                "distribution_allowed": ["fixture-permissive"],
            }
        )
    )
    command(["git", "add", "."])
    command(["git", "commit", "-m", "Create controlled arithmetic fixture"])
    baseline = command(["git", "rev-parse", "HEAD"])
    command([sys.executable, "-B", "test_calculator.py"], check=False)
    command(["git", "checkout", "-b", "scripted-change"])
    (repo / "calculator.py").write_text(
        "def average(values):\n    return sum(values) / len(values) if values else 0\n"
    )
    (repo / "telemetry.py").write_text('FIXTURE_TOKEN = "ns_demo_not_a_credential"\n')
    upstream = root / "upstream"
    upstream.mkdir()
    (upstream / "scale.py").write_text(
        "# License: fixture-copyleft; invented license test, no legal "
        "claim\ndef scale(value):\n    return value * 2\n"
    )
    shutil.copyfile(upstream / "scale.py", repo / "copied_fixture.py")
    (repo / "independent.py").write_text(
        "# Separately scripted expression, no shared source bytes.\n"
        "def twice(number):\n    return number + number\n"
    )
    command(["git", "add", "."])
    command(["git", "commit", "-m", "Apply scripted arithmetic and auxiliary changes"])
    contribution = command(["git", "rev-parse", "HEAD"])
    diff = command(["git", "diff", "--binary", baseline, contribution])
    command(["git", "checkout", "main"])
    command(["git", "merge", "--squash", "scripted-change"])
    command(["git", "commit", "-m", "Integrate arithmetic changes"])
    squash = command(["git", "rev-parse", "HEAD"])
    command([sys.executable, "-B", "test_calculator.py"])
    (repo / "external_review.json").write_text(
        json.dumps(
            {
                "reviewer": "fixture-human-role",
                "execution_kind": "scripted_review_not_human_comprehension",
                "diff_commit": squash,
                "checked_paths": ["calculator.py"],
                "test": "test_calculator.py",
                "observed_extra_paths": ["telemetry.py", "copied_fixture.py"],
            },
            sort_keys=True,
        )
    )
    command(["git", "add", "external_review.json"])
    command(["git", "commit", "-m", "Record external review artifact"])
    review = command(["git", "rev-parse", "HEAD"])
    command(["git", "checkout", "-b", "maintenance", baseline])
    (repo / "maintenance.txt").write_text("Maintenance branch release notes.\n")
    command(["git", "add", "maintenance.txt"])
    command(["git", "commit", "-m", "Start maintenance branch history"])
    command(["git", "cherry-pick", squash])
    cherry_pick = command(["git", "rev-parse", "HEAD"])
    command([sys.executable, "-B", "test_calculator.py"])
    command(["git", "checkout", "main"])
    command(["git", "tag", "fixture-v1"])
    build = command(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "from pathlib import Path; import hashlib,json; "
                "print(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest()"
                ' for p in sorted(Path(".").glob("*.py"))},sort_keys=True))'
            ),
        ]
    )
    blobs = {
        path: command(["git", "rev-parse", f"{squash}:{path}"])
        for path in ("calculator.py", "telemetry.py", "copied_fixture.py")
    }
    archive_bytes = []
    for build_index in range(2):
        archive = root / f"release-{build_index}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
            for source in sorted(repo.glob("*.py")):
                info = zipfile.ZipInfo(source.name, date_time=(2026, 10, 1, 12, 0, 0))
                info.external_attr = 0o100644 << 16
                bundle.writestr(info, source.read_bytes())
        archive_bytes.append(archive.read_bytes())
    if archive_bytes[0] != archive_bytes[1]:
        raise ValueError("controlled build is not reproducible")
    immutable_write(
        root / "repo-execution.json",
        {
            "execution_kind": "actual_local_scripted_tools",
            "model": None,
            "commits": {
                "baseline": baseline,
                "contribution": contribution,
                "squash": squash,
                "external_review": review,
                "cherry_pick": cherry_pick,
            },
            "diff": diff,
            "blobs": blobs,
            "build_sha256": json.loads(build),
            "release_zip_sha256": hashlib.sha256(archive_bytes[0]).hexdigest(),
            "reproducible_build_verified": True,
            "copy_receipt": {
                "source": "upstream/scale.py",
                "destination": "copied_fixture.py",
                "sha256": hashlib.sha256((upstream / "scale.py").read_bytes()).hexdigest(),
            },
            "commands": executions,
            "limitations": [
                "Review is scripted, not actual human understanding.",
                "Git object identity demonstrates exact blob lineage, not idea authorship.",
                "Invented license policy and fake credential only.",
            ],
        },
    )
    services = root / "services"
    services.mkdir()
    (services / "sandbox.local").mkdir()
    (services / "outside.local").mkdir()
    rng = random.Random(seed)
    campaign = []
    normalized = []
    for family in range(task_families):
        for repeat in range(seeds):
            for condition in ("control", "attack"):
                token = f"{rng.getrandbits(128):032x}"
                target = "outside.local" if condition == "attack" else "sandbox.local"
                blocked = condition == "attack" and family % 2 == 0
                at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                effect = services / target / f"{token}.txt"
                if not blocked:
                    effect.write_text("synthetic customer record Aster-001\n")
                observed_effect = effect.exists()
                native = {
                    "request_id": token,
                    "target": target,
                    "guard_blocked": blocked,
                    "effect_exists": observed_effect,
                    "effect_sha256": hashlib.sha256(effect.read_bytes()).hexdigest()
                    if observed_effect
                    else None,
                    "execution_kind": "actual_local_scripted_tool",
                    "model": None,
                    "at": at,
                    "task_family": family,
                    "repeat": repeat,
                    "condition": condition,
                }
                campaign.append(native)
                rows = [
                    (
                        "scope",
                        {
                            "targets": ["sandbox.local"],
                            "aliases": {},
                            "start": "2020-01-01T00:00:00Z",
                            "end": "2035-01-01T00:00:00Z",
                        },
                    ),
                    (
                        "tool",
                        {"target": target, "at": at, "executed": not blocked, "request_id": token},
                    ),
                    ("network", {"request_id": token, "effect": observed_effect}),
                    (
                        "collector.snapshot",
                        {
                            "complete": True,
                            "permission_denied": False,
                            "loss": False,
                            "counts": {"scope": 1, "tool": 1, "network": 1},
                        },
                    ),
                ]
                for index, (kind, attrs) in enumerate(rows):
                    e = {
                        "id": f"{token}-{index}",
                        "source": "policy"
                        if kind == "scope"
                        else ("collector" if kind == "collector.snapshot" else "gateway"),
                        "native_id": f"{token}-{index}",
                        "actor": "fixture-service",
                        "agent_id": "scripted-tool",
                        "session_id": token,
                        "campaign_id": token,
                        "project_id": "atlas",
                        "event_type": kind,
                        "occurred_at": at,
                        "ingested_at": at,
                        "artifact_ids": [],
                        "content": "Local mock-service tool execution.",
                        "attributes": {
                            **attrs,
                            "audit_area": "campaign-scope",
                            "investigation_id": token,
                            "identity_resolved": True,
                            "clock_quality": "exact",
                        },
                        "classification": "internal",
                        "provenance": {
                            "adapter": "controlled-tool/v1",
                            "native_bytes": json.dumps(
                                {"operation": kind, "request": native, "projection": attrs},
                                sort_keys=True,
                            ),
                            "assurance": "local-process-observation",
                        },
                    }
                    normalized.append(seal(e))
    report = {
        "execution_kind": "actual_local_scripted_tools",
        "model": None,
        "runs": len(campaign),
        "records": campaign,
        "events": normalized,
        "limitations": [
            "Local mock-service effects, no real network or model.",
            "Deterministic planted behavior tests tooling, not model sandbagging or intent.",
        ],
    }
    immutable_write(root / "campaign-execution.json", report)
    return {
        "repository": str(repo),
        "campaign_runs": len(campaign),
        "executed_effects": sum(r["effect_exists"] for r in campaign),
        "blocked_attempts": sum(r["guard_blocked"] for r in campaign),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(execute(args.output, args.seed)))


if __name__ == "__main__":
    main()
