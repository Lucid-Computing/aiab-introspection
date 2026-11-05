"""Durable, fail-closed consent and disclosure boundary.

All actor/authorization/runtime arguments are assertions by trusted integration code,
NOT credentials. Never populate them directly from an HTTP request body. See
``docs/WORKBENCH_GOVERNANCE_API.md`` for the trust and projection contract.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def canonical(value):
    return rfc8785.dumps(value)


def _same(left, right):
    return canonical(left) == canonical(right)


def commitment(domain, value):
    return hashlib.sha256(domain.encode() + b"\x00" + canonical(value)).hexdigest()


def query_digest(plan):
    """Fixed query-family commitment, approved by the independent release authority."""
    return commitment(
        "aiab-governance/query/v1",
        {key: plan[key] for key in ("mode", "question", "criteria", "scope", "steps", "agent")},
    )


def _plan_digest(plan):
    # Evidence owns this module; import lazily so concurrent implementations need
    # no duplicated contract or schema identifier.
    from .contracts import plan_digest, validate_plan

    validate_plan(plan)
    return plan_digest(plan)


def _now():
    return datetime.now(UTC).timestamp()


def _expiry(value):
    if value is None:
        return float("inf")
    if isinstance(value, bool):
        raise ValueError("invalid expiry")
    if isinstance(value, (int, float)):
        return value
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("expiry requires timezone")
    return parsed.timestamp()


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _ids(values):
    _require(isinstance(values, list) and bool(values), "explicit nonempty identities required")
    _require(all(isinstance(v, str) and v for v in values), "invalid identity")
    _require(len(set(values)) == len(values), "duplicate identity")
    return sorted(values)


def _json(value):
    return canonical(value).decode()


class GovernanceStore:
    """A local SQLite database; each mutation uses one BEGIN IMMEDIATE transaction."""

    def __init__(self, path):
        self.path = str(Path(path))
        _require(self.path != ":memory:", "durable database path required")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS objects (
                    kind TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, previous TEXT NOT NULL,
                    body TEXT NOT NULL, hash TEXT NOT NULL UNIQUE);
                CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
                    BEGIN SELECT RAISE(ABORT,'append only'); END;
                CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
                    BEGIN SELECT RAISE(ABORT,'append only'); END;
            """)
            # executescript commits the transaction opened by _db(). Reacquire
            # the write lock before first-key creation, including concurrent opens.
            db.execute("BEGIN IMMEDIATE")
            if self._get(db, "key", "receipt", required=False) is None:
                key = Ed25519PrivateKey.generate()
                self._put(
                    db,
                    "key",
                    "receipt",
                    {"private": base64.b64encode(key.private_bytes_raw()).decode()},
                )

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout=30000")
            # journal_mode's first-file lock upgrade can fail immediately despite
            # busy_timeout. Serialize mode initialization across processes; normal
            # policy transactions still use SQLite's own locking.
            descriptor = os.open(self.path + ".init.lock", os.O_CREAT | os.O_RDWR, 0o600)
            with os.fdopen(descriptor, "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _get(db, kind, ident, required=True):
        row = db.execute("SELECT body FROM objects WHERE kind=? AND id=?", (kind, ident)).fetchone()
        if row is None:
            _require(not required, "unknown " + kind)
            return None
        return json.loads(row[0])

    @staticmethod
    def _all(db, kind):
        return [
            json.loads(r[0])
            for r in db.execute("SELECT body FROM objects WHERE kind=? ORDER BY id", (kind,))
        ]

    @staticmethod
    def _put(db, kind, ident, value):
        db.execute(
            "INSERT INTO objects VALUES(?,?,?) ON CONFLICT(kind,id) "
            "DO UPDATE SET body=excluded.body",
            (kind, ident, _json(value)),
        )

    def _event(self, db, kind, plan_digest=None, **data):
        row = db.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        previous = row[0] if row else "0" * 64
        body = {"kind": kind, "plan_digest": plan_digest, "time": _now(), **data}
        digest = commitment("aiab-governance/event/v1", {"previous": previous, "body": body})
        cursor = db.execute(
            "INSERT INTO events(previous,body,hash) VALUES(?,?,?)", (previous, _json(body), digest)
        )
        return {"seq": cursor.lastrowid, "hash": digest}

    def enroll_identity(self, actor, public_key, *, roles=()):
        """Trusted identity administrator only. Key/role changes require a new identity."""
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True))
        _require(isinstance(actor, str) and actor, "identity required")
        value = {"id": actor, "public_key": public_key, "roles": sorted(set(roles))}
        with self._db() as db:
            old = self._get(db, "identity", actor, False)
            _require(old is None or _same(old, value), "identity is immutable")
            if old is None:
                self._put(db, "identity", actor, value)
                self._event(db, "identity_enrolled", actor=actor)

    def _role(self, db, actor, role):
        identity = self._get(db, "identity", actor)
        _require(role in identity["roles"], "required authority role: " + role)

    def freeze_roster(self, roster_id, subjects, *, actor, source_commitment, scope_id):
        """Independent authority freezes the full population, never the requester."""
        subjects = _ids(subjects)
        _require(source_commitment and scope_id, "authoritative roster provenance required")
        value = {
            "id": roster_id,
            "subjects": subjects,
            "authority": actor,
            "source_commitment": source_commitment,
            "scope_id": scope_id,
        }
        with self._db() as db:
            self._role(db, actor, "roster_authority")
            for subject in subjects:
                self._get(db, "identity", subject)
            for old in self._all(db, "roster"):
                if old["scope_id"] == scope_id:
                    _require(
                        _same(old, value), "roster scope is frozen; no replacement or shrinking"
                    )
            old = self._get(db, "roster", roster_id, False)
            _require(old is None or _same(old, value), "roster is immutable")
            if old is None:
                self._put(db, "roster", roster_id, value)
                self._event(
                    db,
                    "roster_frozen",
                    roster_id=roster_id,
                    commitment=commitment("aiab-governance/roster/v1", value),
                )
        return value

    def configure_release_policy(
        self, domain, *, actor, families, max_releases=1, min_group_size=2
    ):
        """Independent release authority fixes exact query families and global limits.

        families = {family_id: {subjects: [...], fields: ['counts', 'status']}}.
        Families with intersecting subjects cannot both be admitted, even across
        plan versions, dataset snapshots, recipients or requesting accounts.
        """
        _require(isinstance(max_releases, int) and max_releases > 0, "invalid release bound")
        _require(isinstance(min_group_size, int) and min_group_size > 0, "invalid group bound")
        _require(isinstance(families, dict) and bool(families), "fixed families required")
        for family in families.values():
            _ids(family["subjects"])
            _require(
                isinstance(family.get("query_digest"), str) and family["query_digest"],
                "fixed query commitment required",
            )
            _require(
                set(family["fields"]) <= {"counts", "status", "coverage", "findings"},
                "unsupported family fields",
            )
        value = {
            "domain": domain,
            "families": families,
            "max_releases": max_releases,
            "min_group_size": min_group_size,
            "authority": actor,
        }
        with self._db() as db:
            self._role(db, actor, "release_authority")
            old = self._get(db, "policy", domain, False)
            _require(old is None or _same(old, value), "release policy is immutable")
            if old is None:
                self._put(db, "policy", domain, value)
                self._event(
                    db,
                    "release_policy_frozen",
                    domain_commitment=commitment("aiab-governance/domain/v1", domain),
                )
        return value

    def register_plan(
        self, plan, owner, *, dataset_manifest=None, authorization=None, roster_id=None
    ):
        """Register an exact plan and (optionally) its admission binding.

        Drafts may omit manifest/authorization but cannot be approved/admitted.
        Re-register the same draft with both to freeze its binding before signing.
        authorization: {dataset_ids, subjects, privacy_domain, grant_id, expires_at}.
        The caller verifies source permissions and provenance of this assertion.
        """
        digest = _plan_digest(plan)
        with self._db() as db:
            identity = self._get(db, "identity", owner)
            mode = plan["mode"]
            subjects = _ids(plan["scope"]["subjects"])
            if mode == "personal":
                _require(
                    subjects == [owner] and "employer" not in identity["roles"],
                    "personal audit must be owned by its authenticated subject",
                )
                _require(
                    all(r["id"] == owner for r in plan["disclosure"]["recipients"]),
                    "personal recipients must be the owner",
                )
            roster = None
            if mode == "confidential":
                roster = self._get(db, "roster", roster_id)
                _require(subjects == roster["subjects"], "plan must include full frozen roster")
                _require(sorted(plan["approval"]["roster"]) == subjects, "wrong plan roster")
                _require(roster["authority"] != owner, "roster must be independently authorized")
            required = sorted(
                set([owner] + subjects + plan["approval"].get("required_signers", []))
            )
            for signer in required:
                self._get(db, "identity", signer)
            recipients = plan["disclosure"]["recipients"]
            _require(
                bool(recipients) and len({r["id"] for r in recipients}) == len(recipients),
                "explicit unique recipients required",
            )
            for recipient in recipients:
                self._get(db, "identity", recipient["id"])
                allowed = {"counts", "status", "coverage"}
                if mode != "confidential":
                    allowed.add("findings")
                _require(set(recipient["fields"]) <= allowed, "unsupported recipient fields")
                if mode == "confidential":
                    _require(
                        not recipient.get("allow_evidence") and not recipient.get("allow_excerpts"),
                        "confidential aggregate only",
                    )
            bound = dataset_manifest is not None and authorization is not None
            if bound:
                _require(
                    set(authorization["subjects"]) == set(subjects), "authorization subjects differ"
                )
                _require(
                    sorted(authorization["dataset_ids"]) == sorted(plan["scope"]["dataset_ids"])
                    and bool(authorization["dataset_ids"]),
                    "exact dataset authorization required",
                )
                _require(
                    authorization.get("grant_id") and authorization.get("privacy_domain"),
                    "source grant and stable privacy domain required",
                )
                _require(_expiry(authorization.get("expires_at")) > _now(), "source grant expired")
                grant_key = commitment(
                    "aiab-governance/grant/v1",
                    [authorization["privacy_domain"], authorization["grant_id"]],
                )
                _require(
                    self._get(db, "revoked_grant", grant_key, False) is None,
                    "source authorization revoked",
                )
                policy = self._get(db, "policy", authorization["privacy_domain"])
                if roster is not None:
                    _require(
                        roster["scope_id"] == authorization["privacy_domain"],
                        "roster and privacy domain differ",
                    )
                _require(
                    policy["authority"] != owner, "release policy must be independently authorized"
                )
                family = plan["disclosure"].get("query_family")
                _require(family in policy["families"], "unapproved query family")
                spec = policy["families"][family]
                _require(spec["query_digest"] == query_digest(plan), "unapproved query semantics")
                _require(set(spec["subjects"]) == set(subjects), "query family subjects differ")
                _require(
                    all(set(r["fields"]) <= set(spec["fields"]) for r in recipients),
                    "query family fields differ",
                )
            binding = {
                "plan_digest": digest,
                "dataset_manifest": dataset_manifest,
                "authorization": authorization,
                "recipients": recipients,
                "roster": roster,
                "required_signers": required,
            }
            value = {
                "digest": digest,
                "plan": plan,
                "owner": owner,
                "binding": binding,
                "binding_digest": commitment("aiab-governance/binding/v1", binding),
                "bound": bound,
                "invalidated": False,
            }
            old = self._get(db, "plan", digest, False)
            _require(
                old is None or _same(old, value) or (not old["bound"] and old["owner"] == owner),
                "binding is immutable",
            )
            current_id = owner + ":" + plan["id"]
            current = self._get(db, "current", current_id, False)
            _require(
                old is None or current is None or current["digest"] == digest,
                "historical plan cannot be reactivated with old approvals",
            )
            if not _same(old, value):
                self._put(db, "plan", digest, value)
                self._event(
                    db,
                    "plan_registered",
                    digest,
                    owner=owner,
                    binding_digest=value["binding_digest"],
                )
            self._put(db, "current", current_id, {"digest": digest})
        return {"plan": plan, "digest": digest, "binding_digest": value["binding_digest"]}

    def approval_payload(self, plan_digest, actor, *, expires_at=None, nonce):
        with self._db() as db:
            record = self._get(db, "plan", plan_digest)
            _require(record["bound"], "draft has no input binding")
            _require(actor in record["binding"]["required_signers"], "not a required signer")
            return self._approval_payload(record, actor, expires_at, nonce)

    @staticmethod
    def _approval_payload(record, actor, expires_at, nonce):
        _require(isinstance(nonce, str) and bool(nonce), "approval nonce required")
        return {
            "domain": "aiab-governance/approval/v1",
            "actor": actor,
            "plan_digest": record["digest"],
            "binding_digest": record["binding_digest"],
            "expires_at": expires_at,
            "nonce": nonce,
        }

    def approve(self, plan_digest, actor, *, signature, nonce, expires_at=None):
        with self._db() as db:
            record = self._get(db, "plan", plan_digest)
            self._live_plan(db, record)
            _require(
                record["bound"] and actor in record["binding"]["required_signers"],
                "missing input binding or unauthorized signer",
            )
            _require(_expiry(expires_at) > _now(), "approval expired")
            payload = self._approval_payload(record, actor, expires_at, nonce)
            identity = self._get(db, "identity", actor)
            try:
                Ed25519PublicKey.from_public_bytes(
                    base64.b64decode(identity["public_key"], validate=True)
                ).verify(base64.b64decode(signature, validate=True), canonical(payload))
            except (InvalidSignature, ValueError, TypeError) as exc:
                raise ValueError("invalid Ed25519 approval") from exc
            replay_id = commitment("aiab-governance/approval-nonce/v1", [actor, nonce])
            _require(self._get(db, "nonce", replay_id, False) is None, "approval replay")
            self._put(db, "nonce", replay_id, {"used": True})
            self._put(
                db,
                "approval",
                plan_digest + ":" + actor,
                {"payload": payload, "signature": signature, "revoked": False},
            )
            self._event(
                db, "approved", plan_digest, actor=actor, binding_digest=record["binding_digest"]
            )
        return {"approved": True}

    def revoke(self, plan_digest, actor):
        with self._db() as db:
            record = self._get(db, "plan", plan_digest)
            _require(actor in record["binding"]["required_signers"], "not a required signer")
            approval = self._get(db, "approval", plan_digest + ":" + actor, False)
            if approval is not None:
                approval["revoked"] = True
                self._put(db, "approval", plan_digest + ":" + actor, approval)
            # Blocks pending runs even if the subject later signs again.
            for run in self._all(db, "run"):
                if run["plan_digest"] == plan_digest:
                    run["blocked"] = True
                    self._put(db, "run", run["id"], run)
            self._event(db, "revoked", plan_digest, actor=actor)
        return {"revoked": True}

    def invalidate_authorization(self, plan_digest, *, actor):
        with self._db() as db:
            self._role(db, actor, "source_authority")
            record = self._get(db, "plan", plan_digest)
            authorization = record["binding"]["authorization"]
            _require(authorization is not None, "draft has no source grant")
            grant = [authorization["privacy_domain"], authorization["grant_id"]]
            self._put(
                db,
                "revoked_grant",
                commitment("aiab-governance/grant/v1", grant),
                {"revoked": True},
            )
            for candidate in self._all(db, "plan"):
                auth = candidate["binding"]["authorization"]
                if auth and [auth["privacy_domain"], auth["grant_id"]] == grant:
                    candidate["invalidated"] = True
                    self._put(db, "plan", candidate["digest"], candidate)
                    self._event(
                        db, "source_authorization_invalidated", candidate["digest"], actor=actor
                    )

    def _live_plan(self, db, record):
        current = self._get(db, "current", record["owner"] + ":" + record["plan"]["id"])
        _require(current["digest"] == record["digest"], "stale plan")
        _require(not record["invalidated"], "source authorization revoked")
        _require(_expiry(record["plan"]["approval"].get("expires_at")) > _now(), "plan expired")

    def _consent(self, db, record):
        self._live_plan(db, record)
        _require(record["bound"], "draft has no input binding")
        _require(
            _expiry(record["binding"]["authorization"].get("expires_at")) > _now(),
            "source grant expired",
        )
        for actor in record["binding"]["required_signers"]:
            approval = self._get(db, "approval", record["digest"] + ":" + actor, False)
            _require(
                approval is not None
                and not approval["revoked"]
                and _expiry(approval["payload"]["expires_at"]) > _now()
                and approval["payload"]["binding_digest"] == record["binding_digest"],
                "approval not ready",
            )

    def readiness(self, plan_digest, actor):
        with self._db() as db:
            record = self._get(db, "plan", plan_digest)
            self._visible(db, record, actor)
            try:
                self._consent(db, record)
                ready = True
            except ValueError:
                ready = False
            return {"ready": ready}  # Deliberately no signer names, counts or refusal reason.

    @staticmethod
    def _runtime(plan, facts):
        if plan["mode"] != "confidential":
            return
        _require(
            isinstance(facts, dict)
            and all(
                facts.get(key) is True
                for key in (
                    "attestation_verified",
                    "sealed_input_verified",
                    "client_verified",
                    "plaintext_ui_absent",
                    "protected_execution_verified",
                )
            ),
            "unsupported confidential runtime: verified sealed input and execution required",
        )
        _require(
            facts.get("runtime_digest")
            and facts.get("input_commitment")
            and facts.get("expires_at") is not None
            and _expiry(facts["expires_at"]) > _now(),
            "runtime binding or freshness missing",
        )
        _require(facts.get("plan_digest") == _plan_digest(plan), "runtime plan mismatch")

    def admit(
        self, plan, actor, dataset_manifest, *, request_id, authorization, runtime_facts=None
    ):
        digest = _plan_digest(plan)
        with self._db() as db:
            record = self._get(db, "plan", digest)
            _require(actor == record["owner"], "only owner may run")
            self._consent(db, record)
            _require(
                _same(dataset_manifest, record["binding"]["dataset_manifest"])
                and _same(authorization, record["binding"]["authorization"]),
                "input authorization mismatch",
            )
            self._runtime(plan, runtime_facts)
            if plan["mode"] == "confidential":
                _require(
                    runtime_facts["input_commitment"]
                    == commitment("aiab-governance/dataset-manifest/v1", dataset_manifest),
                    "runtime input mismatch",
                )
            _require(isinstance(request_id, str) and bool(request_id), "run request id required")
            replay = commitment("aiab-governance/request/v1", [actor, request_id])
            _require(self._get(db, "request", replay, False) is None, "duplicate run request")
            domain = authorization["privacy_domain"]
            policy = self._get(db, "policy", domain)
            runs = self._all(db, "run")
            domain_runs = [r for r in runs if r["privacy_domain"] == domain]
            _require(len(domain_runs) < policy["max_releases"], "global release budget exhausted")
            plan_runs = [r for r in runs if r["plan_digest"] == digest]
            _require(
                len(plan_runs) < plan["disclosure"]["max_releases"], "plan release budget exhausted"
            )
            subjects = plan["scope"]["subjects"]
            _require(
                not any(set(subjects) & set(r["subjects"]) for r in domain_runs),
                "overlapping release reservation forbidden",
            )
            run_id = uuid.uuid4().hex
            run = {
                "id": run_id,
                "plan_digest": digest,
                "binding_digest": record["binding_digest"],
                "owner": actor,
                "privacy_domain": domain,
                "subjects": subjects,
                "request_id": request_id,
                "runtime_facts": runtime_facts,
                "approval_manifest": {
                    who: self._get(db, "approval", digest + ":" + who)
                    for who in record["binding"]["required_signers"]
                },
                "blocked": False,
                "released_to": [],
                "result_digest": None,
                "status": "reserved",
            }
            self._put(db, "run", run_id, run)
            self._put(db, "request", replay, {"run_id": run_id})
            self._event(
                db,
                "admitted_and_reserved",
                digest,
                run_id=run_id,
                actor=actor,
                binding_digest=record["binding_digest"],
            )
        return {"run_id": run_id, "binding_digest": record["binding_digest"]}

    def revalidate(self, run_id):
        """Runtime calls between tools; cancellation/revocation remains effective."""
        with self._db() as db:
            run = self._get(db, "run", run_id)
            self._revalidate(db, run)
        return {"authorized": True}

    def _revalidate(self, db, run):
        _require(not run["blocked"], "run blocked or revoked")
        record = self._get(db, "plan", run["plan_digest"])
        self._consent(db, record)
        _require(
            all(
                _expiry(a["payload"]["expires_at"]) > _now()
                for a in run["approval_manifest"].values()
            ),
            "original admission approval expired",
        )
        self._runtime(record["plan"], run["runtime_facts"])
        return record

    def cancel(self, run_id, actor):
        with self._db() as db:
            run = self._get(db, "run", run_id)
            _require(actor == run["owner"], "only owner may cancel")
            run["blocked"] = True
            run["status"] = "cancelled"
            self._put(db, "run", run_id, run)
            self._event(db, "cancelled", run["plan_digest"], run_id=run_id, actor=actor)
        return {"cancelled": True}

    def _project(self, record, result, recipient, policy):
        """Construct fresh schemas; never recursively copy arbitrary result fields."""
        plan = record["plan"]
        spec = next((r for r in plan["disclosure"]["recipients"] if r["id"] == recipient), None)
        _require(spec is not None, "unapproved recipient")
        findings = result.get("findings", [])
        _require(isinstance(findings, list), "invalid findings")
        allowed_outcomes = {"supported", "contradicted", "no_finding", "insufficient_evidence"}
        _require(
            all(isinstance(f, dict) and f.get("outcome") in allowed_outcomes for f in findings),
            "invalid finding outcome",
        )
        minimum = max(policy["min_group_size"], plan["disclosure"]["min_group_size"])
        # No data-derived small-cell threshold: every nonzero cell in (0,k) causes
        # suppression of the ENTIRE counts object (including totals/complements).
        counts = {o: sum(f["outcome"] == o for f in findings) for o in sorted(allowed_outcomes)}
        suppressed = len(plan["scope"]["subjects"]) < minimum or any(
            0 < n < minimum for n in counts.values()
        )
        # Confidential aggregate values must be trusted, independently validated
        # subject counts, not arbitrary finding multiplicity. One finding/subject
        # is required by the runtime's aggregate validator (see release).
        out = {"release_state": "suppressed" if suppressed else "released"}
        for field in spec["fields"]:
            if suppressed and field != "status":
                out[field] = {"state": "suppressed", "value": None}
            elif field == "status":
                out[field] = "completed" if result.get("status") == "completed" else "incomplete"
            elif field == "counts":
                out[field] = {"state": "released", "values": counts}
            elif field == "coverage":
                # Coverage has no free-form source names, raw records, or nested metadata.
                coverage = result.get("coverage", {})
                out[field] = {
                    "state": "available" if coverage.get("complete") is True else "unknown"
                }
            elif field == "findings":
                public = []
                for index, finding in enumerate(findings):
                    safe = {"id": "finding-" + str(index + 1), "outcome": finding["outcome"]}
                    for key in ("criterion_id", "title", "claim", "method"):
                        if isinstance(finding.get(key), str):
                            safe[key] = finding[key]
                    if isinstance(finding.get("limitations"), list):
                        safe["limitations"] = [
                            v for v in finding["limitations"] if isinstance(v, str)
                        ]
                    # Evidence IDs/excerpts require caller's reference-level authorization.
                    # This implementation releases neither; allow_evidence is an upper bound.
                    public.append(safe)
                out[field] = public
        return out

    def release(self, run_id, result, recipient, *, actor=None, authorization, validation=None):
        """Atomically validate, account, sign and store an immutable recipient package.

        actor must equal authenticated recipient (or trusted delivery service uses
        that recipient identity). validation is trusted runtime evidence, never HTTP
        input: {binding_digest, result_digest, aggregate_subjects_verified: true}.
        """
        _require(actor == recipient, "authenticated recipient required")
        with self._db() as db:
            run = self._get(db, "run", run_id)
            record = self._revalidate(db, run)
            _require(
                _same(authorization, record["binding"]["authorization"]),
                "source authorization mismatch",
            )
            _require(recipient not in run["released_to"], "duplicate release")
            result_digest = commitment("aiab-governance/result/v1", result)
            _require(run["result_digest"] in (None, result_digest), "run result substitution")
            if record["plan"]["mode"] == "confidential":
                _require(
                    isinstance(validation, dict)
                    and validation.get("aggregate_subjects_verified") is True
                    and validation.get("binding_digest") == record["binding_digest"]
                    and validation.get("result_digest") == result_digest,
                    "independent aggregate validation required",
                )
                _require(
                    len(result.get("findings", [])) == len(run["subjects"]),
                    "aggregate must classify each authorized subject exactly once",
                )
            policy = self._get(db, "policy", run["privacy_domain"])
            projection = self._project(record, result, recipient, policy)
            release_id = uuid.uuid4().hex
            event = self._event(
                db,
                "released",
                run["plan_digest"],
                run_id=run_id,
                release_id=release_id,
                recipient=recipient,
                projection_digest=commitment("aiab-governance/projection/v1", projection),
            )
            payload = {
                "version": "aiab-governance/receipt/v1",
                "release_id": release_id,
                "run_id": run_id,
                "recipient": recipient,
                "plan_digest": run["plan_digest"],
                "binding_digest": record["binding_digest"],
                "approval_manifest_digest": commitment(
                    "aiab-governance/approval-manifest/v1", run["approval_manifest"]
                ),
                "dataset_manifest_digest": commitment(
                    "aiab-governance/dataset-manifest/v1", record["binding"]["dataset_manifest"]
                ),
                "projection_digest": commitment("aiab-governance/projection/v1", projection),
                "checkpoint": event,
            }
            key = self._key(db)
            receipt = {
                "payload": payload,
                "signature": base64.b64encode(key.sign(canonical(payload))).decode(),
            }
            package = {"projection": projection, "receipt": receipt}
            self._put(db, "release", release_id, package)
            run["released_to"].append(recipient)
            run["result_digest"] = result_digest
            run["status"] = "released"
            self._put(db, "run", run_id, run)
        return package

    def _key(self, db):
        return Ed25519PrivateKey.from_private_bytes(
            base64.b64decode(self._get(db, "key", "receipt")["private"])
        )

    def receipt_public_key(self):
        with self._db() as db:
            return base64.b64encode(self._key(db).public_key().public_bytes_raw()).decode()

    @staticmethod
    def verify_receipt(package, public_key, *, expected=None):
        """Pin public_key independently; an embedded key is never a trust anchor."""
        try:
            payload = package["receipt"]["payload"]
            Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True)).verify(
                base64.b64decode(package["receipt"]["signature"], validate=True), canonical(payload)
            )
            _require(
                payload["projection_digest"]
                == commitment("aiab-governance/projection/v1", package["projection"]),
                "altered projection",
            )
            _require(payload["version"] == "aiab-governance/receipt/v1", "wrong receipt version")
            if expected:
                _require(
                    all(payload.get(k) == v for k, v in expected.items()),
                    "receipt binding mismatch",
                )
        except (InvalidSignature, KeyError, TypeError) as exc:
            raise ValueError("invalid receipt") from exc
        return True

    def checkpoint(self):
        with self._db() as db:
            row = db.execute("SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            return {"seq": row[0], "hash": row[1]} if row else {"seq": 0, "hash": "0" * 64}

    def verify_ledger(self, *, external_checkpoint=None):
        """Compare against an independently stored/witnessed checkpoint to detect rollback."""
        with self._db() as db:
            previous = "0" * 64
            seq = 0
            seen = {0: previous}
            for index, prev, body, digest in db.execute(
                "SELECT seq,previous,body,hash FROM events ORDER BY seq"
            ):
                _require(index == seq + 1 and prev == previous, "broken event lineage")
                _require(
                    digest
                    == commitment(
                        "aiab-governance/event/v1", {"previous": prev, "body": json.loads(body)}
                    ),
                    "altered event",
                )
                seq, previous = index, digest
                seen[seq] = digest
            if external_checkpoint is not None:
                _require(
                    seen.get(external_checkpoint["seq"]) == external_checkpoint["hash"],
                    "external checkpoint mismatch or rollback",
                )
            return {
                "verified": True,
                "seq": seq,
                "hash": previous,
                "externally_anchored": external_checkpoint is not None,
            }

    def _visible(self, db, record, actor):
        self._get(db, "identity", actor)
        if record["plan"]["mode"] == "personal":
            _require(actor == record["owner"], "not found")
        else:
            _require(
                actor
                in set(record["binding"]["required_signers"])
                | {r["id"] for r in record["binding"]["recipients"]},
                "not found",
            )

    def history(self, actor, *, plan_digest=None):
        """Minimal accountability feed; never approval/refusal identities or counts."""
        with self._db() as db:
            visible = set()
            for record in self._all(db, "plan"):
                if plan_digest is not None and record["digest"] != plan_digest:
                    continue
                try:
                    self._visible(db, record, actor)
                    visible.add(record["digest"])
                except ValueError:
                    pass
            if plan_digest is not None:
                _require(plan_digest in visible, "not found")
            output = []
            # Suppress ALL approval/revocation events, including timing/correlation metadata.
            allowed = {"plan_registered", "admitted_and_reserved", "released", "cancelled"}
            for (body,) in db.execute("SELECT body FROM events ORDER BY seq"):
                event = json.loads(body)
                if event["plan_digest"] in visible and event["kind"] in allowed:
                    output.append({k: event[k] for k in ("kind", "plan_digest", "time")})
            return output

    def plans(self, actor):
        """Role-safe discovery for HTTP bootstrap/listing; never use raw SQLite tables."""
        with self._db() as db:
            out = []
            for record in self._all(db, "plan"):
                try:
                    self._visible(db, record, actor)
                except ValueError:
                    continue
                out.append({"plan": record["plan"], "digest": record["digest"]})
            return out

    def run_status(self, run_id, actor):
        with self._db() as db:
            run = self._get(db, "run", run_id)
            record = self._get(db, "plan", run["plan_digest"])
            self._visible(db, record, actor)
            _require(actor == run["owner"] or actor in run["released_to"], "not found")
            packages = [
                p
                for p in self._all(db, "release")
                if p["receipt"]["payload"]["run_id"] == run_id
                and p["receipt"]["payload"]["recipient"] == actor
            ]
            return {
                "run_id": run_id,
                "status": "unavailable" if run["blocked"] else run["status"],
                "releases": packages,
            }

    def challenge(
        self,
        run_id,
        actor,
        *,
        claim_id,
        correction_commitment,
        requested_remedy,
        recipients,
        parent_id=None,
        disposition=None,
        corrected_release_id=None,
    ):
        """Append immutable, authorized lineage. Corrections are separately governed releases.

        No private free text is copied into the ledger. The caller stores authorized
        correction evidence behind correction_commitment. Disposition requires an
        independent challenge_reviewer, not owner or challenger.
        """
        _require(
            claim_id and correction_commitment and requested_remedy, "challenge evidence required"
        )
        _require(
            disposition in (None, "upheld", "amended", "withdrawn", "unresolved"),
            "invalid disposition",
        )
        recipients = _ids(recipients)
        with self._db() as db:
            run = self._get(db, "run", run_id)
            record = self._get(db, "plan", run["plan_digest"])
            self._visible(db, record, actor)
            permitted = set(record["binding"]["required_signers"]) | {
                r["id"] for r in record["binding"]["recipients"]
            }
            _require(set(recipients) <= permitted, "challenge recipient expansion")
            parent = None
            if parent_id:
                parent = self._get(db, "challenge", parent_id)
                _require(
                    parent["run_id"] == run_id
                    and parent["claim_id"] == claim_id
                    and actor in parent["recipients"],
                    "wrong or invisible challenge parent",
                )
                _require(
                    set(recipients) <= set(parent["recipients"]), "challenge visibility expansion"
                )
            if disposition:
                _require(parent is not None, "disposition requires parent")
                self._role(db, actor, "challenge_reviewer")
                _require(
                    actor not in (run["owner"], parent["actor"], parent["origin_actor"]),
                    "reviewer conflict",
                )
            if corrected_release_id:
                _require(disposition == "amended", "correction requires amended disposition")
                package = self._get(db, "release", corrected_release_id)
                payload = package["receipt"]["payload"]
                corrected = self._get(db, "run", payload["run_id"])
                _require(
                    payload["recipient"] in recipients
                    and corrected["id"] != run_id
                    and corrected["privacy_domain"] == run["privacy_domain"]
                    and corrected["owner"] == run["owner"],
                    "unrelated correction release",
                )
                _require(
                    set(corrected["subjects"]) == set(run["subjects"]),
                    "correction population differs",
                )
            value = {
                "id": uuid.uuid4().hex,
                "run_id": run_id,
                "actor": actor,
                "origin_actor": parent["origin_actor"] if parent else actor,
                "claim_id": claim_id,
                "correction_commitment": correction_commitment,
                "requested_remedy": requested_remedy,
                "recipients": recipients,
                "parent_id": parent_id,
                "disposition": disposition,
                "corrected_release_id": corrected_release_id,
            }
            event = self._event(
                db,
                "challenge_recorded",
                run["plan_digest"],
                run_id=run_id,
                challenge_commitment=commitment("aiab-governance/challenge/v1", value),
            )
            value["ledger_seq"] = event["seq"]
            self._put(db, "challenge", value["id"], value)
        return value

    def challenges(self, run_id, actor):
        with self._db() as db:
            run = self._get(db, "run", run_id)
            self._visible(db, self._get(db, "plan", run["plan_digest"]), actor)
            return sorted(
                [
                    v
                    for v in self._all(db, "challenge")
                    if v["run_id"] == run_id and actor in v["recipients"]
                ],
                key=lambda v: v["ledger_seq"],
            )
