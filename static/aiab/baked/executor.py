"""Parent-owned inference broker and independent validation of worker messages.

The worker runs the plan; this process owns the model. For each model node the worker
sends one batch of texts. The broker checks that the node is a model node of the signed
plan and has not been served before, builds the prompts itself (prompts.py), preflights
every text against the model's context before the first inference of that batch, validates
every answer against the schema it dictated, and only then hands the answers back. Worker
stderr is discarded.

There is no limit on records, bytes, model calls or run time. The model's context window is
a real limit, so the broker tells the worker how many bytes each model node's user message
may have, and the worker splits a longer item into pieces that fit (graph.py). Messages
between the two processes are JSON lines of any length. Plan code gets PLAN_CPU_SECONDS of
CPU time, which waiting for the model does not use: it stops a plan that never finishes, not
a large one.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

from aiab.audit.protocol import AuditError, canonical, decode, validate
from aiab.baked import prompts
from aiab.baked.output import check_result
from aiab.baked.plan_ir import is_model_node, model_settings
from aiab.baked.worker import STAGES

PLAN_CPU_SECONDS = 3600
# Pipe lines carry every record at once; asyncio refuses a line longer than its stream limit.
LINE_LIMIT = sys.maxsize


def budgets(nodes: dict, model) -> dict[str, int]:
    """Per model node, the largest user message (UTF-8 bytes) that fits the model's context
    next to the node's system prompt and answer budget. Empty for a model that does not say
    (the development stand-in), so nothing is split."""
    measure = getattr(model, "user_budget", None)
    if measure is None:
        return {}
    out = {}
    for node in nodes.values():
        if is_model_node(node):
            system = prompts.request(node, "")[0][0]["content"]
            out[node["id"]] = measure(system, model_settings(node)["max_output_tokens"])
    return out


async def execute(bundle, records, model):
    cfg = bundle.config
    nodes = {n["id"]: n for n in bundle.plan["nodes"]}
    worker = None
    try:
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
            limit=LINE_LIMIT,
        )

        async def send(value):
            worker.stdin.write(canonical(value) + b"\n")
            await worker.stdin.drain()

        async def receive():
            raw = await worker.stdout.readline()
            if not raw.endswith(b"\n"):
                if not raw and await worker.wait() == -signal.SIGXCPU:
                    raise AuditError("execution_timeout")  # plan code used PLAN_CPU_SECONDS
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
                "budgets": budgets(nodes, model),
                "cpu_seconds": PLAN_CPU_SECONDS,
            }
        )
        served = set()
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
                or not is_model_node(node)
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
            budget = model_settings(node)["max_output_tokens"]
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
    except AuditError:
        raise
    except Exception:
        raise AuditError("execution_failed") from None
    finally:
        if worker is not None:
            if worker.returncode is None:
                worker.kill()
            await worker.wait()
