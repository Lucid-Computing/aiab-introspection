"""Disposable computation. Trusted dependencies load before installing seccomp."""

import collections
import itertools
import json
import math
import os
import sqlite3
import statistics
import sys

from aiab.baked.worker import isolate


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
    request = json.loads(sys.stdin.buffer.readline(2_097_153))
    operation = request.get("operation", "python")
    events = request["events"]
    if operation == "python":
        code = compile(request["code"], "<audit-analysis>", "exec")
        namespace = {
            "events": events,
            "json": json,
            "math": math,
            "statistics": statistics,
            "collections": collections,
            "itertools": itertools,
        }

        def compute():
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
    isolate(3)
    encoded = json.dumps(compute(), allow_nan=False)
    if len(encoded.encode()) > 262144:
        raise ValueError("analysis_output_limit")
    print(encoded, flush=True)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        os._exit(1)
