"""Scoped indexed evidence, with no ambient filesystem/network or SQL extensions.

The caller MUST resolve and intersect authorization before construction. ``scope``
requires nonempty dataset_ids, subjects, fields, and sources (``required_sources``
is accepted as the source allowlist). Empty/unresolved collections never mean all.
Time bounds are inclusive; unknown chronology is excluded for bounded scopes.
Fields are top-level atomic grants: granting attributes/content grants their full
value, so the caller must resolve mixed-subject records before supplying a corpus.

Optional methods: ``join(left_ids, right_ids, on=...)``, ``temporal(...)``,
``temporal_join(...)``, ``artifact_events(id)``, ``resolve_identity(alias)``,
``reference(id, field, start, end)``,
``fetch_reference(ref)``, ``fetch_native(id)``. Raw access additionally requires
``allow_native=True`` and every event field authorized. All routes use the same
immutable authorized projection. SQL sees only that projection in an in-memory
indexed table named ``events``; nested JSON is stored as canonical JSON text.
Search pagination is explicit; oversized results raise, never silently truncate.
Coverage is scoped and never treats an empty search as proof of absence. Its
negative-claim flag is only an evidence-coverage prerequisite; substantive negative
conclusions still require criterion-specific method and counterevidence validation.
"""

import base64
import copy
import hashlib
import math
import sqlite3
import time

from .contracts import (
    EVENT_FIELDS,
    canonical_json,
    digest,
    timestamp,
    validate_dataset,
    validate_scope,
)


class EvidenceError(ValueError):
    """Typed denial/invalid evidence/resource error; code is safe for API mapping."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


class EvidenceStore:
    def __init__(
        self,
        dataset,
        scope=None,
        *,
        max_results=1000,
        max_result_bytes=4 * 1024 * 1024,
        max_sql_steps=100_000,
        max_seconds=2,
        allow_native=False,
        native_verifier=None,
    ):
        validate_dataset(dataset)
        if not isinstance(scope, dict):
            raise EvidenceError("unresolved_authorized_scope")
        scope = copy.deepcopy(scope)
        scope.setdefault("required_sources", scope.get("sources", []))
        validate_scope(scope)
        sources = scope.get("sources", scope["required_sources"])
        if not isinstance(sources, list) or any(not isinstance(s, str) or not s for s in sources):
            raise EvidenceError("invalid_source_scope")
        if not all(scope.get(k) for k in ["dataset_ids", "subjects", "fields"]) or not sources:
            raise EvidenceError("unresolved_authorized_scope")
        if dataset["id"] not in scope["dataset_ids"]:
            raise EvidenceError("dataset_not_authorized")
        if (
            any(
                type(n) is not int or n <= 0 for n in [max_results, max_result_bytes, max_sql_steps]
            )
            or type(max_seconds) not in (int, float)
            or not math.isfinite(max_seconds)
            or max_seconds <= 0
        ):
            raise EvidenceError("invalid_resource_limits")
        self._verification = {}
        if native_verifier is not None:
            from .ingest import validate_native_bindings

            validate_native_bindings(dataset)
            # The verifier is trusted code supplied by the admission layer. Feed the
            # complete native snapshot before any scope filtering or normalization.
            for descriptor in dataset["manifest"]["sources"]:
                if not isinstance(descriptor, dict):
                    raise EvidenceError("native_descriptor_required")
                hashes = descriptor.get("record_hashes", [])
                raw_index = {r["sha256"]: r for r in dataset.get("native_records", [])}
                try:
                    raw = tuple(
                        base64.b64decode(raw_index[h]["bytes_base64"], validate=True)
                        for h in hashes
                    )
                except (KeyError, ValueError) as exc:
                    raise EvidenceError("native_snapshot_missing") from exc
                verified = native_verifier.verify(raw, copy.deepcopy(descriptor))
                if (
                    not isinstance(verified, dict)
                    or not {"authenticity", "coverage"} <= verified.keys()
                ):
                    raise EvidenceError("invalid_native_verification_result")
                self._verification[descriptor["id"]] = copy.deepcopy(verified)
        self._origin = dataset["origin"]
        self._scope = scope
        self._sources = frozenset(sources)
        self._fields = frozenset(scope["fields"])
        self._max_results, self._max_bytes = max_results, max_result_bytes
        self._max_steps, self._seconds = max_sql_steps, max_seconds
        self._allow_native = allow_native and self._fields == set(EVENT_FIELDS)
        self._records = {}
        self._native = {}
        self._analysis_records = {}
        self._selection_restricted = False
        self._unknown_time = 0
        self._unknown_identity = False
        self._loss = False
        self._searched, self._examined, self._fully_examined = set(), set(), set()
        start, end = scope["time_start"], scope["time_end"]
        start, end = timestamp(start) if start else None, timestamp(end) if end else None
        native_index = {r.get("sha256"): r for r in dataset.get("native_records", [])}
        for event in dataset["events"]:
            if event["source"] not in self._sources:
                continue
            if event["actor"] is None:
                native_moment = timestamp(event["occurred_at"]) if event["occurred_at"] else None
                if native_moment and (
                    (start and native_moment < start) or (end and native_moment > end)
                ):
                    continue
                # Never assign an unknown actor to an authorized lookalike. No
                # unknown-subject counts or content are exposed by coverage.
                self._unknown_identity = True
                continue
            if event["actor"] not in scope["subjects"]:
                # Do not expose counts about subjects outside authorization.
                continue
            moment = timestamp(event["occurred_at"]) if event["occurred_at"] else None
            if moment is None:
                self._unknown_time += 1
                if start or end:
                    continue
            if moment and ((start and moment < start) or (end and moment > end)):
                continue
            self._loss |= bool(event["provenance"].get("loss"))
            self._records[event["id"]] = {
                k: copy.deepcopy(v) for k, v in event.items() if k in self._fields
            }
            # Inline bytes may contain fields outside this projection. Only the
            # fixed sandboxed analyzer can obtain whole originals below.
            if "provenance" in self._records[event["id"]]:
                self._records[event["id"]]["provenance"].pop("native_bytes", None)
            if self._fields == set(EVENT_FIELDS):
                self._analysis_records[event["id"]] = copy.deepcopy(event)
            if self._allow_native:
                raw_hash = event["provenance"].get("raw_sha256")
                if raw_hash in native_index:
                    self._native[event["id"]] = copy.deepcopy(native_index[raw_hash])
        self._manifest = copy.deepcopy(dataset["manifest"])
        # Identity maps are not exported. Resolution only returns authorized identities
        # and provenance references that themselves resolve inside this store.
        self._identities = copy.deepcopy(dataset["manifest"].get("identities", []))
        self._build_index()

    def _build_index(self):
        self._db = sqlite3.connect(":memory:")
        self._db.enable_load_extension(False)
        self._db.execute("PRAGMA temp_store=MEMORY")
        self._db.execute("PRAGMA threads=0")
        # Corpus rows may exceed an individual response budget. Span retrieval
        # remains available while SQL output is independently bounded below.
        row_bytes = max((len(canonical_json(e)) for e in self._records.values()), default=0)
        self._db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, max(self._max_bytes, row_bytes + 4096))
        self._db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 100_000)
        self._db.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 50)
        self._db.setlimit(sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 20)
        columns = sorted(self._fields)
        self._columns = columns
        self._db.execute("CREATE TABLE events (" + ",".join(f'"{c}"' for c in columns) + ")")
        self._db.executemany(
            "INSERT INTO events VALUES (" + ",".join("?" for _ in columns) + ")",
            [tuple(self._sql_value(e.get(c)) for c in columns) for e in self._records.values()],
        )
        for column in ["id", "source", "actor", "occurred_at", "session_id", "native_id"]:
            if column in columns:
                self._db.execute(f'CREATE INDEX "idx_{column}" ON events ("{column}")')
        self._db.commit()
        self._db.set_authorizer(self._authorize_sql)

    @staticmethod
    def _sql_value(value):
        return canonical_json(value).decode() if isinstance(value, (dict, list)) else value

    def _authorize_sql(self, action, arg1, arg2, database, trigger):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if (
            action == sqlite3.SQLITE_READ
            and arg1 == "events"
            and (not arg2 or arg2 in self._fields)
        ):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and arg2.lower() in {
            "count",
            "sum",
            "avg",
            "min",
            "max",
            "length",
            "lower",
            "upper",
            "coalesce",
            "nullif",
            "abs",
            "round",
            "substr",
            "like",
            "glob",
            "json_extract",
            "json_type",
        }:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def _bounded(self, result):
        if len(result) > self._max_results or len(canonical_json(result)) > self._max_bytes:
            raise EvidenceError("result_limit_exceeded")
        return copy.deepcopy(result)

    @property
    def events(self):
        """A defensive copy of the scoped projection, bounded like all other results."""
        result = self._bounded(list(self._records.values()))
        self._examined.update(self._records)
        self._fully_examined.update(self._records)
        return result

    def iter_event_pages(self):
        """Stream bounded projection pages without claiming examination.

        Exhaustion means complete iteration; a single oversized record fails.
        """
        page, size = [], 2
        for event in self._records.values():
            event_size = len(canonical_json(event)) + 1
            if event_size + 2 > self._max_bytes:
                raise EvidenceError("result_limit_exceeded")
            if page and (len(page) >= self._max_results or size + event_size > self._max_bytes):
                yield copy.deepcopy(page)
                page, size = [], 2
            page.append(event)
            size += event_size
        if page:
            yield copy.deepcopy(page)

    def analysis_events(self, ids):
        """Private originals for the fixed sandboxed domain analyzer ONLY.

        Partial field grants cannot authorize whole-record commitment checks.
        Native revisions and original normalized commitments are preserved.
        """
        if self._fields != set(EVENT_FIELDS):
            raise EvidenceError("analysis_requires_complete_field_grant")
        if any(i not in self._analysis_records for i in ids):
            raise EvidenceError("reference_missing_or_unauthorized")
        return [copy.deepcopy(self._analysis_records[i]) for i in ids]

    def restrict_ids(self, ids):
        """Narrow a validated snapshot without rewriting its native manifest."""
        ids = set(ids)
        if not ids <= self._records.keys():
            raise EvidenceError("reference_missing_or_unauthorized")
        view = copy.copy(self)
        view._selection_restricted = self._selection_restricted or ids != self._records.keys()
        for name in ("_records", "_native", "_analysis_records"):
            setattr(view, name, {k: v for k, v in getattr(self, name).items() if k in ids})
        view._searched, view._examined, view._fully_examined = set(), set(), set()
        view._build_index()
        return view

    def search(self, query="", *, examine=True, **filters):
        if not isinstance(query, str):
            raise EvidenceError("invalid_search")
        paginated = "limit" in filters or "offset" in filters
        limit = filters.pop("limit", self._max_results)
        offset = filters.pop("offset", 0)
        if (
            type(limit) is not int
            or not 1 <= limit <= self._max_results
            or type(offset) is not int
            or offset < 0
        ):
            raise EvidenceError("invalid_pagination")
        for field in filters:
            if field not in self._fields:
                raise EvidenceError("field_not_authorized")
        results, scanned = [], set()
        deadline = time.monotonic() + self._seconds
        for key, event in self._records.items():
            if time.monotonic() > deadline:
                raise EvidenceError("search_resource_limit")
            scanned.add(key)
            if any(
                event.get(k) not in v if isinstance(v, list) else event.get(k) != v
                for k, v in filters.items()
            ):
                continue
            # Never match secret fields and then return an authorized projection.
            if query.casefold() not in canonical_json(event).decode().casefold():
                continue
            results.append(event)
        # Explicit pagination is required if a caller wants a partial page.
        if not paginated and len(results) > limit:
            raise EvidenceError("result_limit_exceeded")
        page = results[offset : offset + limit]
        result = self._bounded(page)
        # IDs may themselves be unprojected; associate rows internally without
        # introducing ungranted IDs into the returned projection.
        self._searched.update(scanned)
        if examine:
            returned = {id(event) for event in page}
            ids = {k for k, v in self._records.items() if id(v) in returned}
            self._examined.update(ids)
            self._fully_examined.update(ids)
        return result

    def fetch(self, ids):
        if isinstance(ids, str):
            ids = [ids]
        if not isinstance(ids, (list, tuple)) or any(not isinstance(i, str) for i in ids):
            raise EvidenceError("invalid_references")
        if len(ids) > self._max_results:
            raise EvidenceError("result_limit_exceeded")
        if any(i not in self._records for i in ids):
            raise EvidenceError("reference_missing_or_unauthorized")
        result = self._bounded([self._records[i] for i in ids])
        self._examined.update(ids)
        self._fully_examined.update(ids)
        return result

    def query_sql(self, sql):
        if not isinstance(sql, str) or len(sql) > 100_000:
            raise EvidenceError("invalid_sql")
        deadline, calls = time.monotonic() + self._seconds, 0

        def progress():
            nonlocal calls
            calls += 100
            return calls > self._max_steps or time.monotonic() > deadline

        self._db.set_progress_handler(progress, 100)
        try:
            cursor = self._db.execute(sql)
            if cursor.description is None:
                raise EvidenceError("sql_not_read_only")
            names = [c[0] for c in cursor.description]
            if len(set(names)) != len(names):
                raise EvidenceError("duplicate_sql_columns")
            result, size = [], 0
            for row in cursor:
                item = dict(zip(names, row, strict=True))
                size += len(canonical_json(item))
                if len(result) >= self._max_results or size > self._max_bytes:
                    raise EvidenceError("result_limit_exceeded")
                result.append(item)
            return self._bounded(result)
        except (sqlite3.Error, ValueError) as exc:
            if isinstance(exc, EvidenceError):
                raise
            raise EvidenceError("sql_denied_or_resource_limit") from exc
        finally:
            self._db.set_progress_handler(None, 0)

    def _record(self, event_id):
        if not isinstance(event_id, str) or event_id not in self._records:
            raise EvidenceError("reference_missing_or_unauthorized")
        return self._records[event_id]

    def reference(self, event_id, field="content", start=0, end=None):
        """Use Unicode code point offsets and bind the whole authorized projection."""
        event = self._record(event_id)
        if field not in self._fields or not isinstance(event.get(field), str):
            raise EvidenceError("reference_field_unavailable")
        end = len(event[field]) if end is None else end
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start <= end <= len(event[field])
        ):
            raise EvidenceError("invalid_reference_span")
        return {
            "event_id": event_id,
            "field": field,
            "start": start,
            "end": end,
            "event_digest": digest("authorized-event", event),
        }

    def fetch_reference(self, reference):
        try:
            expected = self.reference(
                reference["event_id"], reference["field"], reference["start"], reference["end"]
            )
            if expected != reference:
                raise EvidenceError("reference_commitment_mismatch")
            event = self._record(reference["event_id"])
            excerpt = event[reference["field"]][reference["start"] : reference["end"]]
            self._bounded([excerpt])
            self._examined.add(reference["event_id"])
            return excerpt
        except (KeyError, TypeError) as exc:
            raise EvidenceError("invalid_reference") from exc

    def fetch_native(self, event_id, *, start=0, end=None):
        self._record(event_id)
        if not self._allow_native:
            raise EvidenceError("native_fields_not_authorized")
        if event_id not in self._native:
            raise EvidenceError("native_bytes_unavailable")
        record = self._native[event_id]
        try:
            raw = base64.b64decode(record["bytes_base64"], validate=True)
        except ValueError as exc:
            raise EvidenceError("invalid_native_bytes") from exc
        if hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise EvidenceError("native_commitment_mismatch")
        end = len(raw) if end is None else end
        if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(raw):
            raise EvidenceError("invalid_reference_span")
        if end - start > self._max_bytes:
            raise EvidenceError("result_limit_exceeded")
        self._examined.add(event_id)
        if start == 0 and end == len(raw):
            self._fully_examined.add(event_id)
        return raw[start:end]

    def join(self, left_ids, right_ids, *, on, right_on=None):
        right_on = right_on or on
        if on not in self._fields or right_on not in self._fields:
            raise EvidenceError("field_not_authorized")
        left, right = self.fetch(left_ids), self.fetch(right_ids)
        index = {}
        for row in right:
            if row.get(right_on) is not None:
                index.setdefault(canonical_json(row[right_on]), []).append(row)
        pairs = []
        for row in left:
            if row.get(on) is None:
                continue
            for match in index.get(canonical_json(row[on]), []):
                pairs.append({"left": row, "right": match})
                self._bounded(pairs)
        return self._bounded(pairs)

    def temporal(self, ids, *, anchor, before_seconds=0, after_seconds=0):
        if "occurred_at" not in self._fields:
            raise EvidenceError("field_not_authorized")
        if before_seconds < 0 or after_seconds < 0:
            raise EvidenceError("invalid_time_window")
        anchor = timestamp(anchor)
        result = []
        for event in self.fetch(ids):
            if event.get("occurred_at") is None:
                raise EvidenceError("unknown_chronology")
            delta = (timestamp(event["occurred_at"]) - anchor).total_seconds()
            if -before_seconds <= delta <= after_seconds:
                result.append(event)
        return self._bounded(sorted(result, key=lambda e: timestamp(e["occurred_at"])))

    def temporal_join(self, left_ids, right_ids, *, on, before_seconds=0, after_seconds=0):
        """Exact entity join with a bounded right-event time window around each left event.

        Unknown timestamps are explicit errors; no ordering is fabricated. Returns
        authorized event pairs and signed observed time differences in seconds.
        """
        from bisect import bisect_left, bisect_right

        if not {on, "occurred_at"} <= self._fields:
            raise EvidenceError("field_not_authorized")
        if before_seconds < 0 or after_seconds < 0:
            raise EvidenceError("invalid_time_window")
        left, right = self.fetch(left_ids), self.fetch(right_ids)
        index = {}
        for row in right:
            if row.get("occurred_at") is None:
                raise EvidenceError("unknown_chronology")
            if row.get(on) is not None:
                index.setdefault(canonical_json(row[on]), []).append(
                    (timestamp(row["occurred_at"]).timestamp(), row)
                )
        for values in index.values():
            values.sort(key=lambda entry: entry[0])
        result = []
        deadline = time.monotonic() + self._seconds
        for row in left:
            if row.get("occurred_at") is None:
                raise EvidenceError("unknown_chronology")
            if row.get(on) is None:
                continue
            anchor = timestamp(row["occurred_at"]).timestamp()
            values = index.get(canonical_json(row[on]), [])
            lo = bisect_left(values, anchor - before_seconds, key=lambda entry: entry[0])
            hi = bisect_right(values, anchor + after_seconds, key=lambda entry: entry[0])
            for moment, other in values[lo:hi]:
                if time.monotonic() > deadline:
                    raise EvidenceError("join_resource_limit")
                result.append({"left": row, "right": other, "delta_seconds": moment - anchor})
                self._bounded(result)
        return self._bounded(result)

    def artifact_events(self, artifact_id):
        """Return only authorized records explicitly referencing an artifact/version ID.

        This is observed provenance, not a claim that similarity proves lineage.
        Registry metadata is not implicitly authorized by an event-field grant.
        """
        if "artifact_ids" not in self._fields:
            raise EvidenceError("field_not_authorized")
        ids = [
            key for key, row in self._records.items() if artifact_id in row.get("artifact_ids", [])
        ]
        if not ids:
            raise EvidenceError("artifact_missing_or_unauthorized")
        return self.fetch(ids)

    def resolve_identity(self, alias):
        if not {"actor", "provenance", "id"} <= self._fields:
            raise EvidenceError("field_not_authorized")
        matches = [m for m in self._identities if alias in m.get("aliases", [])]
        # Assess ambiguity before projecting: never choose an authorized lookalike.
        if len(matches) != 1 or matches[0].get("uncertain"):
            raise EvidenceError("identity_ambiguous_or_unknown")
        mapping = matches[0]
        if mapping.get("subject") not in self._scope["subjects"]:
            raise EvidenceError("identity_not_authorized")
        refs = mapping.get("evidence_ids", [])
        if not refs:
            raise EvidenceError("identity_provenance_missing")
        self.fetch(refs)
        return self._bounded([{"subject": mapping["subject"], "evidence_ids": refs}])[0]

    def coverage(self):
        manifest = self._manifest
        declared = {s if isinstance(s, str) else s.get("id") for s in manifest["sources"]}
        missing = sorted((self._sources | set(self._scope["required_sources"])) - declared)
        assertions = manifest["coverage"]
        # Exact scope binding is required: global "complete" must not be reused for
        # another population/window or inferred from the supplied event list.
        bound = assertions.get("scope") == self._scope and not self._selection_restricted
        independent = bool(assertions.get("universe")) and bool(assertions.get("inventory_digest"))
        limitations = []
        if missing:
            limitations.append("missing_required_sources")
        if self._loss:
            limitations.append("lossy_or_truncated_evidence")
        if not self._records:
            limitations.append("empty_authorized_population")
        if self._unknown_time:
            limitations.append("unknown_chronology")
        if self._unknown_identity:
            limitations.append("identity_coverage_unverified")
        if not bound or not independent:
            limitations.append("independent_scoped_coverage_unverified")
        if assertions.get("status") != "complete":
            limitations.append("collection_incomplete_or_unverified")
        # A native completeness claim requires actual adapter verification results;
        # synthetic declarations may be tested but remain explicitly synthetic.
        relevant = [
            s for s in manifest["sources"] if isinstance(s, dict) and s.get("id") in self._sources
        ]
        native_verified = bool(relevant) and all(
            self._verification.get(s["id"], {}).get("authenticity") == "verified"
            and self._verification.get(s["id"], {}).get("coverage") == "verified"
            for s in relevant
        )
        if self._origin == "synthetic":
            native_verified = False
            limitations.append("synthetic_source_assurance_only")
        if not native_verified or self._sources - {s["id"] for s in relevant}:
            native_verified = False
            limitations.append("native_completeness_unverified")
        collection_complete = not limitations
        population_examined = bool(self._records) and self._fully_examined == set(self._records)
        population_searched = bool(self._records) and self._searched == set(self._records)
        return {
            "status": "complete" if collection_complete else "insufficient_evidence",
            "population_fully_searched": population_searched,
            "population_fully_examined": population_examined,
            "authorized_events": len(self._records),
            "searched_events": len(self._searched),
            "examined_events": len(self._examined),
            "missing_sources": missing,
            "unknown_chronology": self._unknown_time,
            "limitations": limitations,
            "negative_claim_permitted": collection_complete
            and population_examined
            and population_searched,
            "native_verified": native_verified,
        }

    def close(self):
        self._db.close()
