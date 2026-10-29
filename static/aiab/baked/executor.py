"""Parent-owned inference broker and independent validation of worker messages.

The worker runs the plan; this process owns the model. For each model node the worker
sends one batch of texts. The broker checks that the node is a model node of the signed
plan and has not been served before, that the texts and the total number of calls stay
within the plan's limits, builds the prompts itself (prompts.py), preflights every text
against the model's context before the first inference of that batch, validates every
answer against the schema it dictated, and only then hands the answers back. Worker stdout
is bounded protocol input; worker stderr is discarded.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from aiab.audit.protocol import AuditError, canonical, decode, validate
from aiab.baked import prompts
from aiab.baked.output import check_result
from aiab.baked.worker import STAGES

MODEL_NODES = ("classifier", "run_llm")


async def execute(bundle, records, model):
    cfg = bundle.config
    limits = cfg["limits"]
    nodes = {n["id"]: n for n in bundle.plan["nodes"]}
    worker = None
    try:
        async with asyncio.timeout(limits["max_runtime_seconds_per_run"]):
            worker = await asyncio.create_subprocess_exec(
                sys.executable,
                "-B",
                "-m",
                "aiab.baked.worker",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                close_fds=True,
                cwd=str(Path(__file__).resolve().parents[2]),
                env={
                    "PATH": os.defpath,
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONHASHSEED": "0",
                    "LANG": "C.UTF-8",
                },
                limit=2_097_152,
            )

            async def send(value):
                worker.stdin.write(canonical(value) + b"\n")
                await worker.stdin.drain()

            async def receive():
                raw = await worker.stdout.readline()
                if not raw.endswith(b"\n") or len(raw) > 2_097_152:
                    raise AuditError("execution_failed")
                value = decode(raw)
                if isinstance(value, dict) and value.get("kind") == "error":
                    code = value.get("error")
                    if set(value) == {"kind", "error"} and code in {
                        "plan_start_failed",
                        *STAGES.values(),
                    }:
                        raise AuditError(code)
                    raise AuditError("execution_failed")
                return value

            await send(
                {
                    "plan": bundle.plan,
                    "order": list(bundle.order),
                    "records": records,
                    "limits": limits,
                }
            )
            served = set()
            calls = 0
            while True:
                message = await receive()
                if not isinstance(message, dict):
                    raise AuditError("execution_failed")
                if message.get("kind") == "result":
                    if set(message) != {"kind", "result"}:
                        raise AuditError("execution_failed")
                    check_result(message["result"], cfg)
                    worker.stdin.close()
                    if await worker.wait() != 0:
                        raise AuditError("execution_failed")
                    return message["result"]
                if set(message) != {"kind", "node", "texts"} or message["kind"] != "llm":
                    raise AuditError("execution_failed")
                node = nodes.get(message["node"])
                texts = message["texts"]
                if (
                    node is None
                    or node["type"] not in MODEL_NODES
                    or node["id"] in served
                    or not isinstance(texts, list)
                    or any(not isinstance(t, str) for t in texts)
                ):
                    raise AuditError("execution_failed")
                if (
                    node["type"] == "run_llm"
                    and node["config"]["output"]["type"] == "Value"
                    and len(texts) != 1
                ):
                    raise AuditError("execution_failed")
                served.add(node["id"])
                calls += len(texts)
                if calls > limits["max_model_calls_per_run"]:
                    raise AuditError("input_too_large", 413)
                if any(len(t.encode("utf-8")) > limits["max_input_utf8_bytes"] for t in texts):
                    raise AuditError("input_too_large", 413)
                budget = node["config"]["model"]["max_output_tokens"]
                requests = [prompts.request(node, text) for text in texts]
                # Check every complete prompt of this node before any inference, never truncate.
                for content, _ in requests:
                    await model.check_messages(content, budget)
                answers = []
                for content, schema in requests:
                    answer = await model.complete(content, schema, budget)
                    validate(answer, schema, "model_output_invalid")
                    answers.append(prompts.normalize_answer(node, answer))
                await send({"answers": answers})
    except TimeoutError:
        raise AuditError("execution_timeout") from None
    except AuditError:
        raise
    except Exception:
        raise AuditError("execution_failed") from None
    finally:
        if worker is not None:
            if worker.returncode is None:
                worker.kill()
            await worker.wait()
