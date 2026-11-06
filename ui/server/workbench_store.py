"""Durable UI metadata. This database is not a confidential evidence boundary.

Plan revisions and run snapshots are immutable. Every read includes an owner predicate;
recipient disclosure is handled separately by the governance store.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class Conflict(ValueError):
    """A stale editor or competing worker attempted a state transition."""


class WorkbenchStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS plans (
                    owner TEXT NOT NULL, id TEXT NOT NULL, digest TEXT NOT NULL,
                    body TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY(owner,id)
                );
                CREATE TABLE IF NOT EXISTS revisions (
                    owner TEXT NOT NULL, id TEXT NOT NULL, digest TEXT NOT NULL,
                    body TEXT NOT NULL, created REAL NOT NULL,
                    PRIMARY KEY(owner,id,digest)
                );
                CREATE TABLE IF NOT EXISTS datasets (
                    owner TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL,
                    created REAL NOT NULL, PRIMARY KEY(owner,id)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, mode TEXT NOT NULL,
                    plan TEXT NOT NULL, dataset_id TEXT NOT NULL, digest TEXT NOT NULL,
                    status TEXT NOT NULL, result TEXT, error TEXT,
                    governance_id TEXT, created REAL NOT NULL, updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS annotations (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL,
                    body TEXT NOT NULL, created REAL NOT NULL
                );
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def save_plan(
        self, owner, plan, digest, *, expected_digest=None, create=False, before_commit=None
    ):
        body = json.dumps(plan, allow_nan=False)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT digest FROM plans WHERE owner=? AND id=?", (owner, plan["id"])
            ).fetchone()
            if (create and old) or (not create and (not old or old[0] != expected_digest)):
                raise Conflict("Plan changed; reload before saving.")
            if before_commit:
                before_commit()
            db.execute(
                "INSERT OR IGNORE INTO revisions VALUES (?,?,?,?,?)",
                (owner, plan["id"], digest, body, time.time()),
            )
            db.execute(
                "INSERT INTO plans VALUES (?,?,?,?,?) ON CONFLICT(owner,id) DO UPDATE SET "
                "digest=excluded.digest, body=excluded.body, updated=excluded.updated",
                (owner, plan["id"], digest, body, time.time()),
            )
        return {"plan": json.loads(body), "digest": digest}

    def is_current(self, owner, plan):
        try:
            return self.plan(owner, plan["id"])["plan"] == plan
        except KeyError:
            return False

    def current_owner(self, digest):
        with self.connection() as db:
            rows = db.execute("SELECT owner FROM plans WHERE digest=?", (digest,)).fetchall()
        if len(rows) != 1:
            raise KeyError("Agreement not found")
        return rows[0][0]

    def job_header(self, run_id):
        """Trusted routing metadata only; caller MUST authorize before fetching job()."""
        with self.connection() as db:
            row = db.execute(
                "SELECT owner,mode,plan,digest,governance_id,status FROM jobs WHERE id=?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError("Run not found")
        out = dict(row)
        out["plan"] = json.loads(out["plan"])
        return out

    def annotate(self, kind, target, value):
        """Append-only private material; authorization belongs to API integration."""
        value = dict(value, id=value.get("id", uuid.uuid4().hex), created=time.time())
        with self.connection() as db:
            db.execute(
                "INSERT INTO annotations VALUES (?,?,?,?,?)",
                (value["id"], kind, target, json.dumps(value, allow_nan=False), value["created"]),
            )
        return value

    def annotations(self, kind, target, actor):
        with self.connection() as db:
            rows = db.execute(
                "SELECT body FROM annotations WHERE kind=? AND target=? ORDER BY created,id",
                (kind, target),
            ).fetchall()
        return [value for row in rows if actor in (value := json.loads(row[0]))["recipients"]]

    def plan(self, owner, plan_id):
        with self.connection() as db:
            row = db.execute(
                "SELECT body,digest FROM plans WHERE owner=? AND id=?", (owner, plan_id)
            ).fetchone()
        if row is None:
            raise KeyError("Plan not found")
        return {"plan": json.loads(row[0]), "digest": row[1]}

    def execution_snapshot(self, owner, plan_id, expected_digest):
        # The body and digest come from one SELECT, so a competing save cannot
        # pair a checked digest with another revision's body. Callers queue this
        # detached snapshot, never reload the mutable plan ID after admission.
        if not isinstance(expected_digest, str) or not expected_digest.strip():
            raise ValueError("expected_digest is required for execution")
        package = self.plan(owner, plan_id)
        if package["digest"] != expected_digest:
            raise Conflict(
                "Saved agreement changed; expected_digest is stale. Reload and check again."
            )
        return package

    def revision(self, owner, plan_id, digest):
        with self.connection() as db:
            row = db.execute(
                "SELECT body FROM revisions WHERE owner=? AND id=? AND digest=?",
                (owner, plan_id, digest),
            ).fetchone()
        if row is None:
            raise KeyError("Revision not found")
        return {"plan": json.loads(row[0]), "digest": digest}

    def plans(self, owner):
        with self.connection() as db:
            return [
                {"plan": json.loads(r[0]), "digest": r[1]}
                for r in db.execute(
                    "SELECT body,digest FROM plans WHERE owner=? ORDER BY updated DESC", (owner,)
                )
            ]

    def add_dataset(self, owner, dataset):
        try:
            with self.connection() as db:
                db.execute(
                    "INSERT INTO datasets VALUES (?,?,?,?)",
                    (owner, dataset["id"], json.dumps(dataset, allow_nan=False), time.time()),
                )
        except sqlite3.IntegrityError as exc:
            raise Conflict("Dataset IDs are immutable; import under a new ID.") from exc

    def dataset(self, owner, dataset_id):
        with self.connection() as db:
            row = db.execute(
                "SELECT body FROM datasets WHERE owner=? AND id=?", (owner, dataset_id)
            ).fetchone()
        if row is None:
            raise KeyError("Dataset not found")
        return json.loads(row[0])

    def datasets(self, owner):
        with self.connection() as db:
            return [
                {k: v for k, v in json.loads(r[0]).items() if k != "events"}
                for r in db.execute("SELECT body FROM datasets WHERE owner=?", (owner,))
            ]

    def enqueue(self, owner, plan, digest, dataset_id, mode, *, governance_id=None):
        if mode not in {"rehearse", "audit"}:
            raise ValueError("Unknown execution mode")
        run_id, now = uuid.uuid4().hex, time.time()
        with self.connection() as db:
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,'queued',NULL,NULL,?,?,?)",
                (
                    run_id,
                    owner,
                    mode,
                    json.dumps(plan),
                    dataset_id,
                    digest,
                    governance_id,
                    now,
                    now,
                ),
            )
        return run_id

    def job(self, owner, run_id):
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE owner=? AND id=?", (owner, run_id)
            ).fetchone()
        if row is None:
            raise KeyError("Run not found")
        result = dict(row)
        for key in ("plan", "result"):
            result[key] = json.loads(result[key]) if result[key] else None
        return result

    def claim(self, owner, run_id):
        with self.connection() as db:
            changed = db.execute(
                "UPDATE jobs SET status='running',updated=? "
                "WHERE id=? AND owner=? AND status='queued'",
                (time.time(), run_id, owner),
            ).rowcount
        return bool(changed)

    def finish(self, owner, run_id, *, result=None, error=None):
        with self.connection() as db:
            changed = db.execute(
                "UPDATE jobs SET status=?,result=?,error=?,updated=? "
                "WHERE id=? AND owner=? AND status='running'",
                (
                    "failed" if error else "completed",
                    json.dumps(result),
                    error,
                    time.time(),
                    run_id,
                    owner,
                ),
            ).rowcount
        return bool(changed)  # cancellation always wins over a late result

    def cancel(self, owner, run_id):
        self.job(owner, run_id)
        with self.connection() as db:
            return bool(
                db.execute(
                    "UPDATE jobs SET status='cancelled',updated=? "
                    "WHERE id=? AND owner=? AND status IN ('queued','running')",
                    (time.time(), run_id, owner),
                ).rowcount
            )

    def recover(self):
        """Single service startup: never replay a potentially billable investigation."""
        with self.connection() as db:
            return db.execute(
                "UPDATE jobs SET status='interrupted',error=?,updated=? "
                "WHERE status IN ('queued','running')",
                ("Service restarted; start a new authorized run.", time.time()),
            ).rowcount
