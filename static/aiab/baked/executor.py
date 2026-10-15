"""Parent-owned inference broker and independent validation of worker messages."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from aiab.audit.protocol import AuditError, canonical, decode
from aiab.baked.output import check_result


def query_text(query):
    return canonical(query["records"]).decode()


async def execute(bundle, records, model):
    cfg = bundle.config
    worker = None
    try:
        async with asyncio.timeout(cfg["limits"]["max_runtime_seconds_per_run"]):
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
                return decode(raw)

            await send(
                {"config": cfg, "records": records, "source": bundle.files["plan.py"].decode()}
            )
            message = await receive()
            if set(message) != {"kind", "queries"} or message["kind"] != "queries":
                raise AuditError("execution_failed")
            queries = message["queries"]
            if (
                not isinstance(queries, list)
                or len(queries) > cfg["limits"]["max_model_calls_per_run"]
            ):
                raise AuditError("execution_failed")
            allowed = {canonical(record) for record in records}
            encoded_queries = []
            for query in queries:
                if not isinstance(query, dict) or set(query) != {
                    "records",
                    "agent_id",
                    "session_id",
                }:
                    raise AuditError("execution_failed")
                items = query["records"]
                if (
                    not isinstance(items, list)
                    or not 1 <= len(items) <= cfg["limits"]["max_logs_per_query"]
                ):
                    raise AuditError("input_too_large")
                if any(canonical(r) not in allowed for r in items):
                    raise AuditError("execution_failed")
                if query["agent_id"] is not None and any(
                    r["agent_id"] != query["agent_id"] for r in items
                ):
                    raise AuditError("execution_failed")
                if query["session_id"] is not None and any(
                    r["session_id"] != query["session_id"] for r in items
                ):
                    raise AuditError("execution_failed")
                # Check every complete query before any inference, never truncate.
                await model.check_context(query_text(query), bundle.policy)
                encoded_queries.append(canonical(query))
            await send({"ready": True})
            count = 0
            while True:
                message = await receive()
                if message.get("kind") == "result":
                    if set(message) != {"kind", "result"} or count != len(queries):
                        raise AuditError("execution_failed")
                    check_result(message["result"], cfg)
                    worker.stdin.close()
                    if await worker.wait() != 0:
                        raise AuditError("execution_failed")
                    return message["result"]
                if (
                    set(message) != {"kind", "prompt_id", "query"}
                    or message["kind"] != "classify"
                    or count >= len(queries)
                ):
                    raise AuditError("execution_failed")
                if (
                    message["prompt_id"] != cfg["classification"]["prompt_id"]
                    or canonical(message["query"]) != encoded_queries[count]
                ):
                    raise AuditError("execution_failed")
                count += 1
                score = await model.classify(
                    query_text(message["query"]), bundle.policy, model.model
                )
                await send({"score": score, "label": ""})
    except AuditError:
        raise
    except Exception:
        raise AuditError("execution_failed") from None
    finally:
        if worker is not None:
            if worker.returncode is None:
                worker.kill()
            await worker.wait()
