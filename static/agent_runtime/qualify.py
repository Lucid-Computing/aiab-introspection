"""Opt-in REAL DeepSeek/Pi synthetic qualification. Never substitutes a model double.

Run with the runtime environment, from static/:
  python agent_runtime/qualify.py --host HOST --env-file /operator/read-only/.env
Only fixed statuses, public attestation diagnostics and usage are printed. Private
trace/model reasoning and credential bytes are never printed or saved.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from aiab.workbench.model import MODEL_PROFILE, DeepSeekModel
from aiab.workbench.runner import Supervisor, budgets
from aiab.workbench.tools import ToolBroker


class SyntheticEvidence:
    """Explicit synthetic spike fixture, not a native/complete collection claim."""

    events = [
        {
            "id": "evt-1",
            "actor": "synthetic-owner",
            "session_id": "session-1",
            "campaign_id": "synthetic-campaign",
            "occurred_at": "2026-10-01T10:00:00Z",
            "content": "Agent executed a request to example.invalid outside approved scope.",
        },
        {
            "id": "evt-2",
            "actor": "synthetic-owner",
            "session_id": "session-2",
            "campaign_id": "synthetic-campaign",
            "occurred_at": "2026-10-01T09:00:00Z",
            "content": (
                "Effective policy permits only test.invalid. "
                "No exception authorizes example.invalid."
            ),
        },
    ]

    def search(self, query, **filters):
        if filters:
            raise ValueError("unsupported_synthetic_filter")
        terms = query.lower().split()
        return [e for e in self.events if any(t in e["content"].lower() for t in terms)]

    def coverage(self):
        return {
            "origin": "synthetic",
            "complete": False,
            "limitations": ["Two supplied fixture events; no native completeness proof."],
        }


async def qualify(host, credential):
    model = DeepSeekModel(host=host, credential=credential)
    report = {
        "timestamp": datetime.now(UTC).isoformat(),
        "live_inference": False,
        "synthetic_evidence": True,
        "profile": MODEL_PROFILE,
        "host": host,
        "status": "failed",
        "stage": "attestation",
    }
    called = set()

    async def emit(kind, data):
        if kind == "tool_result":
            called.add(data["name"])

    plan = {
        "id": "synthetic-qualification",
        "question": "Was the cross-session synthetic action outside effective scope?",
        "criteria": [{"id": "scope", "description": "Effective policy and executed action"}],
        "scope": {"dataset_ids": ["synthetic"], "subjects": ["synthetic-owner"]},
    }
    usage = {
        "turns": 0,
        "tool_calls": 0,
        "total_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }
    try:
        async with asyncio.timeout(180):
            await model.start()
            report["attestation_verified"] = True
            report["stage"] = "multi_turn_agent"
            supervisor = Supervisor(
                plan, ToolBroker(SyntheticEvidence()), model, budgets(plan), emit, usage
            )
            findings = await supervisor.run_agent(
                {
                    "instructions": (
                        "Qualification: exercise search, fetch evt-1 and evt-2, join the two on "
                        "campaign_id, SQL SELECT count(*) FROM events, Python over both IDs "
                        "with result=len(events), coverage, and counterevidence search. "
                        "Use all seven tools before returning a finding. This is a synthetic test."
                    )
                }
            )
            report["live_inference"] = usage["turns"] > 0
            report["findings_count"] = len(findings)
            if usage["turns"] < 2 or called != {
                "search",
                "fetch",
                "join",
                "sql",
                "python",
                "coverage",
                "counterevidence",
            }:
                raise ValueError("qualification_tools_or_turns_missing")
            report["status"] = "passed"
    except Exception as exc:
        report["error_code"] = getattr(exc, "code", "runtime_qualification_failed")
        secure = model.connection.secure
        if secure is not None:
            doc = secure.get_verification_document()
            if doc:
                report["verification_steps"] = {
                    name: step.status for name, step in doc.steps.items()
                }
                report["attestation_verified"] = doc.security_verified
                # Public verifier diagnostics, not exception text that might carry request data.
                report["attestation_errors"] = {
                    name: step.error for name, step in doc.steps.items() if step.status == "failed"
                }
    finally:
        await model.close()
    report["usage"] = usage
    report["tools_exercised"] = sorted(called)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    credential = os.environ.get("TINFOIL_API_KEY", "")
    if args.env_file:
        for line in args.env_file.read_text().splitlines():
            if line.startswith("TINFOIL_API_KEY="):
                credential = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not credential:
        print(
            json.dumps({"status": "failed", "error_code": "missing_tinfoil_inference_credential"})
        )
        raise SystemExit(1)
    report = asyncio.run(qualify(args.host, credential))
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded)
    print(encoded, end="")
    raise SystemExit(0 if report["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
