"""Independent local collector. Evidence never transits the employer UI.

The CLI has no unverified transport switch. Test transport injection is a Python
API seam only, and requires an explicitly labelled test-attestation profile.
"""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from aiab.audit.transport import PinnedConnection

from .confidential_app import PROFILE, SUBMISSION, TOKEN, b64, check_input, fresh, sign, verify
from .contracts import dataset_digest, plan_digest
from .governance import GovernanceStore, _expiry, canonical, commitment, query_digest
from .model import MODEL_PROFILE


def seal(public_key, payload, principal, request_id):
    ephemeral = X25519PrivateKey.generate()
    shared = ephemeral.exchange(
        X25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True))
    )
    key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=b"aiab-sealed-input/v1"
    ).derive(shared)
    nonce = secrets.token_bytes(12)
    # Matches service v1 AAD, including its JSON Unicode escaping behavior.
    aad = json.dumps(
        {"principal": principal, "request_id": request_id}, sort_keys=True, separators=(",", ":")
    ).encode()
    return {
        "version": "aiab-sealed-input/v1",
        "ephemeral_key": b64(ephemeral.public_key().public_bytes_raw()),
        "nonce": b64(nonce),
        "ciphertext": b64(AESGCM(key).encrypt(nonce, canonical(payload), aad)),
    }


def token(actor, key, audience):
    document = sign(
        {
            "domain": TOKEN,
            "actor": actor,
            "audience": audience,
            "expires_at": time.time() + 300,
            "nonce": secrets.token_hex(16),
        },
        key,
    )
    return b64(canonical(document["payload"])) + "." + document["signature"]


def agreement(bundle, authority_key, digest, *, test_attestation=False):
    """Verify authority/source signatures and derive the exact GovernanceStore binding."""
    profile = verify(bundle, authority_key, PROFILE)
    fresh(profile)
    if profile["assurance"] != (
        "test-attestation" if test_attestation else "signed-deployment-gate"
    ):
        raise ValueError("test/live assurance mismatch")
    entry = next(e for e in profile["plans"] if plan_digest(e["plan"]) == digest)
    identities = {i["id"]: i for i in profile["identities"]}
    if len(identities) != len(profile["identities"]) or profile["model_profile"] != MODEL_PROFILE:
        raise ValueError("unapproved identities/model")
    if _expiry(entry["plan"]["approval"]["expires_at"]) <= time.time():
        raise ValueError("plan expired")
    if _expiry(entry["authorization"]["expires_at"]) <= time.time():
        raise ValueError("source permission expired")
    policy = next(
        p for p in profile["policies"] if p["domain"] == entry["authorization"]["privacy_domain"]
    )
    if (
        policy["actor"] == entry["owner"]
        or "release_authority" not in identities[policy["actor"]]["roles"]
    ):
        raise ValueError("independent release authority required")
    family = policy["families"][entry["plan"]["disclosure"]["query_family"]]
    if (
        family["query_digest"] != query_digest(entry["plan"])
        or sorted(family["subjects"]) != sorted(entry["plan"]["scope"]["subjects"])
        or any(
            set(r["fields"]) - set(family["fields"])
            for r in entry["plan"]["disclosure"]["recipients"]
        )
    ):
        raise ValueError("release policy substitution")
    source = identities[entry["source_authority"]]
    if "source_authority" not in source["roles"] or source["id"] == entry["owner"]:
        raise ValueError("independent source authority required")
    permission = verify(entry["source_grant"], source["public_key"], "aiab-confidential/source/v1")
    fresh(permission)
    for k, expected in (
        ("plan_digest", digest),
        ("scope", entry["plan"]["scope"]),
        ("dataset_manifest", entry["dataset_manifest"]),
        ("authorization", entry["authorization"]),
    ):
        if permission[k] != expected:
            raise ValueError("source permission substitution")
    roster = next(r for r in profile["rosters"] if r["roster_id"] == entry["roster_id"])
    if (
        roster["actor"] == entry["owner"]
        or "roster_authority" not in identities[roster["actor"]]["roles"]
    ):
        raise ValueError("independent roster required")
    subjects = sorted(entry["plan"]["scope"]["subjects"])
    if (
        sorted(roster["subjects"]) != subjects
        or sorted(entry["plan"]["approval"]["roster"]) != subjects
    ):
        raise ValueError("roster substitution")
    required = sorted(
        set(subjects + [entry["owner"]] + entry["plan"]["approval"]["required_signers"])
    )
    frozen_roster = {
        "id": roster["roster_id"],
        "subjects": subjects,
        "authority": roster["actor"],
        "source_commitment": roster["source_commitment"],
        "scope_id": roster["scope_id"],
    }
    binding = {
        "plan_digest": digest,
        "dataset_manifest": entry["dataset_manifest"],
        "authorization": entry["authorization"],
        "recipients": entry["plan"]["disclosure"]["recipients"],
        "roster": frozen_roster,
        "required_signers": required,
    }
    return profile, {**entry, "source_permissions": permission}, binding


def mandate(bundle, authority_key, digest, approvals, *, test_attestation=False):
    """Locally verify every exact human signature; no approval fabrication."""
    profile, entry, binding = agreement(
        bundle, authority_key, digest, test_attestation=test_attestation
    )
    identities = {i["id"]: i for i in profile["identities"]}
    required = binding["required_signers"]
    binding_digest = commitment("aiab-governance/binding/v1", binding)
    approved = set()
    for document in approvals:
        p = document["payload"]
        verify(document, identities[p["actor"]]["public_key"], "aiab-governance/approval/v1")
        if p != {
            "domain": "aiab-governance/approval/v1",
            "actor": p["actor"],
            "plan_digest": digest,
            "binding_digest": binding_digest,
            "expires_at": p["expires_at"],
            "nonce": p["nonce"],
        }:
            raise ValueError("approval binding mismatch")
        if p["expires_at"] is not None:
            # Governance supports ISO times as well as numeric expiry.
            if _expiry(p["expires_at"]) <= time.time():
                raise ValueError("approval expired")
        if p["actor"] in approved or p["actor"] not in required or not p["nonce"]:
            raise ValueError("duplicate or unrequired approval")
        approved.add(p["actor"])
    if approved != set(required):
        raise ValueError("every exact human mandate required")
    return profile, entry, binding_digest


async def collect(
    *,
    bundle,
    authority_key,
    digest,
    approvals,
    dataset,
    host,
    repo,
    release_digest,
    principal,
    principal_key,
    collector,
    collector_key,
    request_id=None,
    connection=None,
    test_attestation=False,
    submit_approvals=True,
):
    profile, entry, binding_digest = mandate(
        bundle, authority_key, digest, approvals, test_attestation=test_attestation
    )
    if (repo, release_digest, host) != (
        profile["repo"],
        profile["release_digest"],
        profile["host"],
    ):
        raise ValueError("explicit approved runtime pins differ")
    check_input(entry["plan"], dataset, entry)
    if principal != entry["owner"] or collector not in profile["collectors"]:
        raise ValueError("unapproved collector or principal")
    identities = {i["id"]: i for i in profile["identities"]}
    for actor, key in ((principal, principal_key), (collector, collector_key)):
        if b64(key.public_key().public_bytes_raw()) != identities[actor]["public_key"]:
            raise ValueError("local identity key mismatch")
    if connection is not None and not test_attestation:
        raise ValueError("transport injection is test-only")
    conn = connection if connection is not None else PinnedConnection(host, repo, release_digest)
    request_id = request_id or secrets.token_hex(24)
    profile_digest = commitment(PROFILE, profile)
    bearer = token(principal, principal_key, profile_digest)
    try:
        # No data, credential or body is transmitted before attested TLS is established.
        await conn.start()
        conn.check()
        status, deployed = await conn.request("GET", "/v1/deployment", token=bearer)
        expected = {
            "profile_digest": profile_digest,
            "input_public_key": profile["input_public_key"],
            "receipt_public_key": profile["receipt_public_key"],
            "release_digest": release_digest,
            "assurance": profile["assurance"],
        }
        if status != 200 or deployed != expected:
            raise ValueError("verified deployment binding mismatch")
        status, input_key = await conn.request("GET", "/v1/input-key", token=bearer)
        if status != 200 or input_key.get("public_key") != profile["input_public_key"]:
            raise ValueError("verified input key substitution")
        # Re-submit actual signed approvals, never manufacture them. Already accepted
        # approvals are rejected as replay; submit consent separately before retries.
        for approval in approvals if submit_approvals else []:
            status, _ = await conn.request("POST", "/v1/consent", canonical(approval), bearer)
            if status != 200:
                raise ValueError("consent not accepted (including replay/revocation)")
        ticket = sign(
            {
                "domain": SUBMISSION,
                "collector": collector,
                "principal": principal,
                "request_id": request_id,
                "plan_digest": digest,
                "input_digest": dataset_digest(dataset),
                "profile_digest": profile_digest,
                "input_public_key": profile["input_public_key"],
                "assurance": profile["assurance"],
                "expires_at": min(profile["expires_at"], time.time() + 600),
            },
            collector_key,
        )
        body = {
            "request_id": request_id,
            "sealed_input": seal(
                input_key["public_key"],
                {"plan": entry["plan"], "dataset": dataset, "collector": ticket},
                principal,
                request_id,
            ),
        }
        status, response = await conn.request("POST", "/v1/runs", canonical(body), bearer)
        if status != 200:
            raise ValueError("confidential admission denied")
        return {
            **response,
            "plan_digest": digest,
            "binding_digest": binding_digest,
            "profile_digest": profile_digest,
            "assurance": profile["assurance"],
        }
    finally:
        await conn.close()


async def recipient_request(
    *,
    bundle,
    authority_key,
    host,
    repo,
    release_digest,
    principal,
    principal_key,
    run_id,
    release=False,
    governance_run_id=None,
    binding_digest=None,
):
    """Fetch status or a governed package, always through pinned actual attestation."""
    profile = verify(bundle, authority_key, PROFILE)
    fresh(profile)
    if profile["assurance"] != "signed-deployment-gate":
        raise ValueError("live attestation profile required")
    if (host, repo, release_digest) != (
        profile["host"],
        profile["repo"],
        profile["release_digest"],
    ):
        raise ValueError("runtime pin mismatch")
    identity = next(i for i in profile["identities"] if i["id"] == principal)
    if b64(principal_key.public_key().public_bytes_raw()) != identity["public_key"]:
        raise ValueError("wrong local principal key")
    # Service IDs are generated hex; reject path injection before any connection.
    import re

    if not re.fullmatch(r"[a-f0-9]{48}", run_id):
        raise ValueError("invalid runtime run id")
    if release:
        if not governance_run_id or not binding_digest:
            raise ValueError("explicit admission bindings required")
        matches = []
        for candidate in profile["plans"]:
            _, candidate_entry, candidate_binding = agreement(
                bundle, authority_key, plan_digest(candidate["plan"])
            )
            if commitment("aiab-governance/binding/v1", candidate_binding) == binding_digest:
                matches.append(candidate_entry)
        if len(matches) != 1:
            raise ValueError("unrecognized admission binding")
        entry = matches[0]
    conn = PinnedConnection(host, repo, release_digest)
    bearer = token(principal, principal_key, commitment(PROFILE, profile))
    try:
        await conn.start()
        conn.check()
        status, deployment = await conn.request("GET", "/v1/deployment", token=bearer)
        if status != 200 or deployment.get("profile_digest") != commitment(PROFILE, profile):
            raise ValueError("deployment binding mismatch")
        path = "/v1/runs/" + run_id + ("/release" if release else "")
        status, result = await conn.request("POST" if release else "GET", path, token=bearer)
        if status != 200:
            raise ValueError("recipient operation denied")
        if release:
            GovernanceStore.verify_receipt(
                result["result"],
                profile["receipt_public_key"],
                expected={
                    "run_id": governance_run_id,
                    "recipient": principal,
                    "binding_digest": binding_digest,
                    "plan_digest": plan_digest(entry["plan"]),
                },
            )
        return result
    finally:
        await conn.close()


def load(path):
    return json.loads(Path(path).read_text())


def local_key(path):
    return Ed25519PrivateKey.from_private_bytes(Path(path).read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    payload_parser = sub.add_parser(
        "approval-payload", help="Derive exact payload from reviewed signed agreement"
    )
    for flag in ("bundle", "authority-key", "plan-digest", "actor", "expires-at"):
        payload_parser.add_argument("--" + flag, required=True)
    signing = sub.add_parser(
        "sign-consent", help="Sign locally reviewed exact GovernanceStore approval payload"
    )
    signing.add_argument("payload")
    signing.add_argument("--key", required=True)
    signing.add_argument("--output", required=True)
    signing.add_argument("--actor", required=True)
    revoking = sub.add_parser("sign-revocation")
    for flag in ("key", "actor", "plan-digest", "profile-digest", "output"):
        revoking.add_argument("--" + flag, required=True)
    revoking.add_argument("--source", action="store_true")
    receipt = sub.add_parser("verify-receipt")
    receipt.add_argument("package")
    for flag in ("public-key", "plan-digest", "binding-digest", "recipient", "run-id"):
        receipt.add_argument("--" + flag, required=True)
    submit = sub.add_parser("collect")
    for flag in (
        "bundle",
        "authority-key",
        "plan-digest",
        "approvals",
        "dataset",
        "host",
        "repo",
        "release-digest",
        "principal",
        "principal-key",
        "collector",
        "collector-key",
    ):
        submit.add_argument("--" + flag, required=True)
    submit.add_argument("--consent-already-submitted", action="store_true")
    for command in ("status", "release"):
        operation = sub.add_parser(command)
        for flag in (
            "bundle",
            "authority-key",
            "host",
            "repo",
            "release-digest",
            "principal",
            "principal-key",
            "run-id",
        ):
            operation.add_argument("--" + flag, required=True)
        if command == "release":
            operation.add_argument("--governance-run-id", required=True)
            operation.add_argument("--binding-digest", required=True)
    args = parser.parse_args()
    if args.command == "approval-payload":
        _, _, binding = agreement(
            load(args.bundle), Path(args.authority_key).read_text().strip(), args.plan_digest
        )
        if args.actor not in binding["required_signers"]:
            parser.error("actor is not an exact required signer")
        print(
            canonical(
                {
                    "domain": "aiab-governance/approval/v1",
                    "actor": args.actor,
                    "plan_digest": args.plan_digest,
                    "binding_digest": commitment("aiab-governance/binding/v1", binding),
                    "expires_at": args.expires_at,
                    "nonce": secrets.token_hex(24),
                }
            ).decode()
        )
    elif args.command == "sign-consent":
        payload = load(args.payload)
        if (
            set(payload)
            != {"domain", "actor", "plan_digest", "binding_digest", "expires_at", "nonce"}
            or payload["domain"] != "aiab-governance/approval/v1"
            or payload["actor"] != args.actor
        ):
            parser.error("must review an exact approval payload for this actor")
        Path(args.output).write_bytes(canonical(sign(payload, local_key(args.key))))
    elif args.command == "sign-revocation":
        payload = {
            "domain": "aiab-confidential/revoke-source/v1"
            if args.source
            else "aiab-confidential/revoke/v1",
            "actor": args.actor,
            "plan_digest": args.plan_digest,
            "profile_digest": args.profile_digest,
        }
        Path(args.output).write_bytes(canonical(sign(payload, local_key(args.key))))
    elif args.command == "verify-receipt":
        GovernanceStore.verify_receipt(
            load(args.package),
            Path(args.public_key).read_text().strip(),
            expected={
                "plan_digest": args.plan_digest,
                "binding_digest": args.binding_digest,
                "recipient": args.recipient,
                "run_id": args.run_id,
            },
        )
        print(
            "receipt signature, projection and explicit bindings verified; "
            "not proof of factual correctness"
        )
    elif args.command in {"status", "release"}:
        import asyncio

        result = asyncio.run(
            recipient_request(
                bundle=load(args.bundle),
                authority_key=Path(args.authority_key).read_text().strip(),
                host=args.host,
                repo=args.repo,
                release_digest=args.release_digest,
                principal=args.principal,
                principal_key=local_key(args.principal_key),
                run_id=args.run_id,
                release=args.command == "release",
                governance_run_id=getattr(args, "governance_run_id", None),
                binding_digest=getattr(args, "binding_digest", None),
            )
        )
        print(canonical(result).decode())
    else:
        import asyncio

        result = asyncio.run(
            collect(
                bundle=load(args.bundle),
                authority_key=Path(args.authority_key).read_text().strip(),
                digest=args.plan_digest,
                approvals=load(args.approvals),
                dataset=load(args.dataset),
                host=args.host,
                repo=args.repo,
                release_digest=args.release_digest,
                principal=args.principal,
                principal_key=local_key(args.principal_key),
                collector=args.collector,
                collector_key=local_key(args.collector_key),
                submit_approvals=not args.consent_already_submitted,
            )
        )
        print(canonical(result).decode())


if __name__ == "__main__":
    main()
