"""One disposable plan process; no keys, credentials, network or filesystem access."""

from __future__ import annotations

import ctypes
import dataclasses
import json
import math
import os
import platform
import resource
import sys
import typing

from aiab import plan_api


def isolate(seconds):
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise RuntimeError("unsupported_sandbox")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (seconds + 1,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    libc = ctypes.CDLL(None, use_errno=True)

    class Filter(ctypes.Structure):
        _fields_ = [
            ("code", ctypes.c_ushort),
            ("jt", ctypes.c_ubyte),
            ("jf", ctypes.c_ubyte),
            ("k", ctypes.c_uint),
        ]

    class Program(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ushort), ("filter", ctypes.POINTER(Filter))]

    # Check audit architecture first (reject x32 numbers by the strict allowlist).
    rules = [(0x20, 0, 0, 4), (0x15, 1, 0, 0xC000003E), (0x06, 0, 0, 0x80000000), (0x20, 0, 0, 0)]
    # read/write stdio; allocator, signals, process exit. No open, sockets, exec,
    # fork/clone, ptrace, process_vm_*, randomness or filesystem mutations.
    for number in (0, 1, 3, 5, 8, 9, 10, 11, 12, 13, 14, 15, 25, 28, 60, 131, 158, 202, 231):
        rules += [(0x15, 0, 1, number), (0x06, 0, 0, 0x7FFF0000)]
    rules.append((0x06, 0, 0, 0x00050000 | 1))  # EPERM
    array = (Filter * len(rules))(*(Filter(*row) for row in rules))
    program = Program(len(rules), array)
    if libc.prctl(38, 1, 0, 0, 0) != 0 or libc.prctl(22, 2, ctypes.byref(program), 0, 0) != 0:
        raise RuntimeError("sandbox_unavailable")


def send(value):
    sys.stdout.write(json.dumps(value, allow_nan=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def receive():
    line = sys.stdin.buffer.readline(2_097_153)
    if not line.endswith(b"\n") or len(line) > 2_097_152:
        raise ValueError
    return json.loads(line)


def main():
    request = receive()
    cfg = request["config"]
    records = [plan_api.LogRecord(**r) for r in request["records"]]
    source = compile(request["source"], "plan/plan.py", "exec")
    # Import dependencies before restricting syscalls; signed module code only
    # runs after the boundary is installed. No parent secrets reach this process.
    assert typing.Literal and dataclasses.dataclass
    isolate(cfg["limits"]["max_runtime_seconds_per_run"])
    namespace = {"__name__": "plan.plan"}
    exec(source, namespace)
    kept = namespace["filter_logs"](
        records,
        rules=plan_api.FilterRules(
            keywords=tuple(cfg["filter"]["keywords"]),
            kinds=tuple(cfg["filter"]["kinds"]),
            agents=tuple(cfg["input_scope"]["agents"]),
        ),
    )
    if (
        not isinstance(kept, list)
        or len(kept) > len(records)
        or any(r not in records for r in kept)
    ):
        raise ValueError
    queries = namespace["build_query"](kept, window=cfg["window"])
    if not isinstance(queries, list) or len(queries) > cfg["limits"]["max_model_calls_per_run"]:
        raise ValueError
    send({"kind": "queries", "queries": [dataclasses.asdict(q) for q in queries]})
    if receive() != {"ready": True}:
        raise ValueError
    calls = 0

    def run_llm(prompt_id, item):
        nonlocal calls
        if prompt_id != cfg["classification"]["prompt_id"] or item not in queries:
            raise ValueError
        calls += 1
        if calls > cfg["limits"]["max_model_calls_per_run"]:
            raise ValueError
        send({"kind": "classify", "prompt_id": prompt_id, "query": dataclasses.asdict(item)})
        response = receive()
        return plan_api.ClassifierResult(**response)

    plan_api.install_run_llm(run_llm)
    scores = [
        namespace["classify"](
            q, model_id=cfg["model"]["id"], prompt_id=cfg["classification"]["prompt_id"]
        )
        for q in queries
    ]
    if calls != len(queries) or any(
        type(s) not in (float, int) or not math.isfinite(s) or not 0 <= s <= 1 for s in scores
    ):
        raise ValueError
    verdict = namespace["metric"](scores, threshold=cfg["classification"]["threshold"])
    send({"kind": "result", "result": namespace["emit_report"](verdict)})


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        # Never reflect the exception or its potentially confidential message.
        os._exit(1)
