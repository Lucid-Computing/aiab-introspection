"""Offline trusted provisioning: python -m server.workbench_admin --help.

Inputs are imported operator authorization assertions, NOT independently verified
facts. This tool never generates a principal's key or signs consent for them.
Run as the service's trusted operator with exclusive source-grant file access.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from aiab.workbench import contracts
from aiab.workbench.governance import GovernanceStore, query_digest

TRUST = "imported_operator_authorization_not_independently_verified"
ROLES = {
    "employer",
    "roster_authority",
    "release_authority",
    "source_authority",
    "challenge_reviewer",
}


def provision(directory, operation, value):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    store = GovernanceStore(directory / "governance.db")
    if value.get("trust") != TRUST:
        raise ValueError("Explicit imported authorization trust acknowledgement required")
    if operation == "identity":
        if not set(value.get("roles", [])) <= ROLES:
            raise ValueError("Unknown role")
        store.enroll_identity(value["actor"], value["public_key"], roles=value.get("roles", []))
    elif operation == "frozenroster":
        store.freeze_roster(
            value["roster_id"],
            value["subjects"],
            actor=value["authority"],
            source_commitment=value["source_commitment"],
            scope_id=value["scope_id"],
        )
    elif operation == "releasepolicy":
        # Families explicitly import approved semantics, with a digest computed from
        # an independently reviewed exact plan rather than ad hoc HTTP claims.
        families = {}
        for name, spec in value["families"].items():
            plan = contracts.validate_plan(spec["plan"])
            families[name] = {
                "subjects": plan["scope"]["subjects"],
                "fields": spec["fields"],
                "query_digest": query_digest(plan),
            }
        store.configure_release_policy(
            value["domain"],
            actor=value["authority"],
            families=families,
            max_releases=value["max_releases"],
            min_group_size=value["min_group_size"],
        )
    elif operation == "sourcegrant":
        # Trusted CLI uses the store's role seam; this is not an HTTP capability.
        with store._db() as db:
            store._role(db, value["authority"], "source_authority")
        plan = contracts.validate_plan(value["plan"])
        auth = value["authorization"]
        if (
            set(auth["dataset_ids"]) != set(plan["scope"]["dataset_ids"])
            or set(auth["subjects"]) != set(plan["scope"]["subjects"])
            or "expires_at" not in auth
            or not auth.get("grant_id")
            or not auth.get("privacy_domain")
            or not value.get("source_commitment")
        ):
            raise ValueError("Exact source authorization and provenance required")
        # Roles are imported through identity above. A sourcegrant import is itself
        # an offline operator authority action; no fabricated signature is attached.
        if not isinstance(value.get("dataset_manifest"), dict):
            raise ValueError("Independently authorized complete dataset manifest required")
        grant = {
            "dataset_manifest": value["dataset_manifest"],
            "owner": value["owner"],
            "plan_digest": contracts.plan_digest(plan),
            "authorization": auth,
            "roster_id": value.get("roster_id"),
            "trust": TRUST,
            "authority": value["authority"],
            "source_commitment": value["source_commitment"],
        }
        path = directory / "source-grants.json"
        rows = json.loads(path.read_text()) if path.exists() else []
        existing = [
            r
            for r in rows
            if r["owner"] == grant["owner"] and r["plan_digest"] == grant["plan_digest"]
        ]
        if existing and existing != [grant]:
            raise ValueError("Source binding immutable; revoke and create a new agreement")
        if not existing:
            rows.append(grant)
        fd, name = tempfile.mkstemp(dir=directory, prefix=".source-grants-")
        try:
            with os.fdopen(fd, "w") as output:
                json.dump(rows, output, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, path)
            parent = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            if os.path.exists(name):
                os.unlink(name)
    elif operation == "revoke-source":
        store.invalidate_authorization(value["plan_digest"], actor=value["authority"])
    else:
        raise ValueError("Unknown provisioning operation")
    return {"imported": True, "trust": TRUST, "operation": operation}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument(
        "operation",
        choices=["identity", "frozenroster", "releasepolicy", "sourcegrant", "revoke-source"],
    )
    parser.add_argument(
        "input", type=Path, help="JSON imported authorization; never private signing keys"
    )
    args = parser.parse_args(argv)
    print(json.dumps(provision(args.directory, args.operation, json.loads(args.input.read_text()))))


if __name__ == "__main__":
    main()
