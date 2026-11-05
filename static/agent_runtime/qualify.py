"""Opt-in REAL DeepSeek/Pi synthetic qualification. Never substitutes a model double.

Run with the runtime environment, from static/:
  python agent_runtime/qualify.py --host HOST --env-file /operator/read-only/.env
Only fixed statuses, public attestation diagnostics and usage are printed. Private
trace/model reasoning and credential bytes are never printed or saved.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from aiab.audit.protocol import canonical
from aiab.workbench.model import BROKER, MODEL_ID, MODEL_PROFILE, DeepSeekModel, GoPinnedConnection
from aiab.workbench.runner import Supervisor, budgets, validate_findings
from aiab.workbench.tools import RuntimeFailure, ToolBroker, events_from


async def tokenizer_parity(model, tools):
    """Compare verified chat/raw tokenization where the server chat route works.

    Tool-call rounds use the same released encoder; remote chat tokenization of
    those rounds is broken in this pinned serving release (ValidatorIterator).
    """
    comparisons = []
    for messages in [
        [
            {"role": "system", "content": "Synthetic test"},
            {"role": "user", "content": "Northstar café ⛵"},
        ],
        [
            {"role": "user", "content": "Synthetic"},
            {
                "role": "assistant",
                "content": "Synthetic answer",
                "reasoning": "Synthetic placeholder text, not model reasoning.",
            },
            {"role": "user", "content": "Follow up"},
        ],
    ]:
        status, result = await model.connection.request(
            "POST",
            "/tokenize",
            canonical(
                {
                    "model": MODEL_ID,
                    "messages": messages,
                    "tools": tools,
                    "add_generation_prompt": True,
                    "chat_template_kwargs": MODEL_PROFILE["inference"]["chat_template_kwargs"],
                }
            ),
            timeout=30,
        )
        if status in (401, 403):
            raise RuntimeFailure("tinfoil_credential_lacks_inference_access")
        rendered_count = await model.count_tokens(messages, tools)
        if status != 200 or type(result.get("count")) is not int:
            raise RuntimeFailure("verified_tokenizer_unavailable")
        if result["count"] != rendered_count:
            raise RuntimeFailure("tokenizer_template_mismatch")
        comparisons.append(
            {"chat_count": result["count"], "released_prompt_count": rendered_count, "equal": True}
        )
    return comparisons


async def qualify(host, credential, transport="python"):
    model = DeepSeekModel(host=host, credential=credential, transport=transport)
    report = {
        "timestamp": datetime.now(UTC).isoformat(),
        "live_inference": False,
        "synthetic_evidence": True,
        "profile": MODEL_PROFILE,
        "host": host,
        "transport": transport,
        "scenario": "northstar-agent-reconciliation-seed17",
        "inference_attempted": False,
        "status": "failed",
        "stage": "attestation",
    }
    if transport == "go" and BROKER.is_file():
        report["broker_sha256"] = hashlib.sha256(BROKER.read_bytes()).hexdigest()
    called = set()

    async def emit(kind, data):
        if kind == "tool_result":
            called.add(data["name"])

    # Use real contracts/evidence projections. Never expose the fixture answer ledger.
    from aiab.workbench import datasets
    from aiab.workbench.evidence import EvidenceStore
    from aiab.workbench.fixtures.generator import build

    dataset, _ = build(17)
    plan = datasets.plan_for("agent-reconciliation", dataset=dataset)
    plan["agent"]["max_seconds"] = 600
    plan["agent"]["max_tokens"] = 262144
    first = next(
        e for e in dataset["events"] if e["attributes"].get("audit_area") == "agent-reconciliation"
    )
    investigation = first["attributes"]["investigation_id"]
    plan["scope"]["required_sources"] = sorted({e["source"] for e in dataset["events"]})
    store = EvidenceStore(dataset, scope=plan["scope"])
    selected = [
        e
        for e in events_from(store)
        if e.get("attributes", {}).get("investigation_id") == investigation
    ]
    ids = [e["id"] for e in selected]
    view = store.restrict_ids(ids)
    broker = ToolBroker(view)
    report["scenario_events"] = len(ids)
    usage = {
        "turns": 0,
        "tool_calls": 0,
        "total_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }
    try:
        async with asyncio.timeout(600):
            await model.start()
            report["attestation_verified"] = True
            report["verification"] = model.verification_document()
            supervisor = Supervisor(plan, broker, model, budgets(plan), emit, usage)
            if transport == "go":
                report["stage"] = "tokenizer_parity"
                report["tokenizer_parity"] = await tokenizer_parity(model, supervisor.tools)
            report["stage"] = "multi_turn_agent"
            findings = await supervisor.run_agent(
                {
                    "instructions": (
                        "Synthetic Northstar qualification: batch all seven independent tool "
                        "calls in your first turn, then use their results on later turns before "
                        'returning findings. Tool arguments are {"request": "<JSON object>"}. '
                        'Search request object: {"query":"Northstar","filters":{"limit":5}}. '
                        "Do not pass limit as a top-level tool request field. Fetch all "
                        f"these authorized event IDs: {json.dumps(ids)}. "
                        'Join request: {"left_ids":[<first ID>],"right_ids":[<second ID>],'
                        '"on":"project_id"}. SQL request: {"sql":"SELECT count(*) FROM events"}. '
                        'Python request: {"ids":[<all supplied IDs>],"code":"result=len(events)"}. '
                        'Coverage request: {}. Counterevidence request: {"query":"amendment"}. '
                        "Seek effective instructions, observed "
                        "edits and shipped release links. Use exactly the declared criterion ID. "
                        "Do not infer complete collection or intent. Final response MUST be one "
                        'JSON object with exactly {"findings":[...]} and no Markdown fences or '
                        "prose outside JSON. Each finding must have id, criterion_id, title, "
                        "outcome, claim, evidence_ids, counterevidence_ids, limitations, method, "
                        'details. method is "agent"; details is an object; limitations is a '
                        "list of strings. Use the evidence IDs exactly, as arrays of strings."
                    )
                }
            )
            validate_findings(findings, plan, broker)
            report["live_inference"] = usage["completion_tokens"] > 0
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
            report["verification_at_completion"] = model.verification_document()
    except Exception as exc:
        report["error_code"] = (
            str(exc)
            if isinstance(exc, RuntimeFailure)
            else getattr(exc, "code", "runtime_qualification_failed")
        )
        if not isinstance(model.connection, GoPinnedConnection):
            secure = model.connection.secure
            if secure is not None:
                doc = secure.get_verification_document()
                if doc:
                    report["verification_steps"] = {
                        name: step.status for name, step in doc.steps.items()
                    }
                    report["attestation_verified"] = doc.security_verified
        # Only fixed error codes are retained, never upstream bodies or exception text.
    finally:
        await model.close()
        view.close()
        store.close()
    report["inference_attempted"] = usage["turns"] > 0
    report["live_inference"] = usage["completion_tokens"] > 0
    report["usage"] = usage
    report["tools_exercised"] = sorted(called)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--transport", choices=("python", "go"), default="python")
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
    report = asyncio.run(qualify(args.host, credential, args.transport))
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded)
    print(encoded, end="")
    raise SystemExit(0 if report["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
