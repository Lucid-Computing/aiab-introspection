"""One disposable plan process; no keys, credentials, network or filesystem access."""

from __future__ import annotations

import ctypes
import json
import os
import platform
import resource
import sys

from aiab.baked import graph

# The fixed code a failure in each kind of node is reported as; nothing else leaves.
STAGES = {
    "input_logs": "plan_query_failed",
    "run_python": "plan_metric_failed",
    "classifier": "plan_classify_failed",
    "run_llm": "plan_classify_failed",
    "final_report": "plan_report_failed",
}


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


stage = "plan_start_failed"


def main():
    global stage
    request = receive()
    plan, order, records = request["plan"], request["order"], request["records"]
    nodes = {n["id"]: n for n in plan["nodes"]}
    # Compile the plan's code and import everything before restricting syscalls; plan code
    # only runs after the boundary is installed. No parent secrets reach this process.
    compiled = {
        nid: compile(nodes[nid]["config"]["code"], f"plan/{nid}.py", "exec")
        for nid in order
        if nodes[nid]["type"] == "run_python"
    }
    for module in ("re", "math", "collections", "itertools", "statistics", "json"):
        __import__(module)
    isolate(request["limits"]["max_runtime_seconds_per_run"])

    def llm(node_id, texts):
        send({"kind": "llm", "node": node_id, "texts": texts})
        response = receive()
        if (
            not isinstance(response, dict)
            or set(response) != {"answers"}
            or not isinstance(response["answers"], list)
            or len(response["answers"]) != len(texts)
        ):
            raise ValueError
        return response["answers"]

    def progress(node):
        global stage
        stage = STAGES[node["type"]]

    result = graph.run_plan(plan, order, records, llm, compiled, progress)
    stage = "plan_report_failed"
    send({"kind": "result", "result": result})


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        # Only a fixed stage code crosses the boundary, never exception text or data.
        try:
            send({"kind": "error", "error": stage})
        finally:
            os._exit(1)
