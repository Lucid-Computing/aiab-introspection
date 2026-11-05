"""Disposable computation. Trusted dependencies load before installing seccomp."""

import collections
import datetime
import itertools
import json
import math
import os
import re
import sqlite3
import statistics
import sys

from aiab.baked.worker import isolate

# Only exact built-in classes qualify. Never serialize an exception's message,
# repr, traceback, source line, attributes or a user-defined subclass name.
PYTHON_ERRORS = {
    SyntaxError: "python_syntax_error",
    IndentationError: "python_syntax_error",
    TabError: "python_syntax_error",
    TypeError: "python_type_error",
    KeyError: "python_key_error",
    NameError: "python_name_error",
    UnboundLocalError: "python_name_error",
    IndexError: "python_index_error",
    AttributeError: "python_attribute_error",
    ValueError: "python_value_error",
    ZeroDivisionError: "python_zero_division_error",
    AssertionError: "python_assertion_error",
}


def prepare_sql(events, sql):
    if not isinstance(sql, str) or len(sql) > 32000:
        raise ValueError("invalid_sql")
    connection = sqlite3.connect(":memory:")
    columns = (
        "id",
        "source",
        "native_id",
        "actor",
        "agent_id",
        "session_id",
        "campaign_id",
        "project_id",
        "event_type",
        "occurred_at",
        "ingested_at",
        "content",
        "classification",
        "attributes",
        "artifact_ids",
        "provenance",
    )
    connection.execute("CREATE TABLE events (" + ",".join(c + " TEXT" for c in columns) + ")")
    connection.executemany(
        "INSERT INTO events VALUES (" + ",".join("?" for _ in columns) + ")",
        [
            [
                json.dumps(e.get(c)) if isinstance(e.get(c), (dict, list)) else e.get(c)
                for c in columns
            ]
            for e in events
        ],
    )
    connection.execute("PRAGMA query_only=ON")
    allowed = {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_RECURSIVE,
    }

    def authorize(action, first, second, database, trigger):
        if action not in allowed or (action == sqlite3.SQLITE_READ and first != "events"):
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and second in {
            "load_extension",
            "readfile",
            "writefile",
        }:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(authorize)
    count = 0

    def progress():
        nonlocal count
        count += 1
        return int(count > 2000)

    connection.set_progress_handler(progress, 1000)

    def query():
        cursor = connection.execute(sql)
        rows = cursor.fetchmany(10001)
        if len(rows) > 10000:
            raise ValueError("sql_output_limit")
        return {"columns": [d[0] for d in cursor.description], "rows": [list(r) for r in rows]}

    return query


def main():
    def read_frame():
        raw = sys.stdin.buffer.readline(2_097_153)
        if len(raw) > 2_097_152 or not raw.endswith(b"\n"):
            raise ValueError("analysis_frame_limit")
        return raw

    header = read_frame()
    request = json.loads(header)
    count = request["event_count"]
    if type(count) is not int or not 0 <= count <= 100000:
        raise ValueError("analysis_event_limit")
    events, size = [], len(header)
    for _ in range(count):
        frame = read_frame()
        size += len(frame)
        if size > 16 * 1024**2:
            raise ValueError("analysis_input_limit")
        events.append(json.loads(frame))
    if sys.stdin.buffer.read(1):
        raise ValueError("analysis_trailing_data")
    operation = request.get("operation", "python")
    if operation == "python":
        # CPython's syntax diagnostics can consult the source filesystem. Compile
        # before seccomp, as before, but retain only syntax failures for feedback
        # after successful sandbox installation. No generated code runs here.
        compile_error = None
        try:
            code = compile(request["code"], "<audit-analysis>", "exec")
        except SyntaxError as exc:
            compile_error = exc
        namespace = {
            "events": events,
            "json": json,
            "math": math,
            "statistics": statistics,
            "collections": collections,
            "itertools": itertools,
            "re": re,
            "datetime": datetime,
        }

        def compute():
            if compile_error is not None:
                raise compile_error
            exec(code, namespace)
            return namespace.get("result")
    elif operation == "sql":
        compute = prepare_sql(events, request["sql"])
    elif operation == "rules":
        from aiab.workbench.analyzers import evaluate

        def compute():
            return evaluate(request["rule"], events, request["config"])
    else:
        raise ValueError("invalid_operation")
    isolate(3, kill_forbidden=operation == "python")
    if operation == "python":
        try:
            result = compute()
        except BaseException as exc:
            error = PYTHON_ERRORS.get(type(exc))
            if error is None:
                raise
            result = {"version": "aiab-python/v1", "status": "error", "code": error}
        else:
            result = {"version": "aiab-python/v1", "status": "ok", "result": result}
    else:
        result = compute()
    # Serialization, nonfinite values and output limits are fatal, not analysis
    # corrections. Keep these outside the narrow execution exception boundary.
    encoded = json.dumps(result, allow_nan=False)
    if len(encoded.encode()) > 262144:
        raise ValueError("analysis_output_limit")
    print(encoded, flush=True)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        os._exit(1)
