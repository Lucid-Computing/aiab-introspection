"""View models over an agreement folder, built on the box's own loader (aiab.baked.bundle).

An agreement is one file, plan.json, the audit plan as a graph. The builder (ui/playground,
embedded in the plan page) edits it and saves it through ``save_plan``, which runs the
box's loader on the candidate first, so nothing the box would refuse is ever written.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiab.audit.protocol import AuditError, canonical, decode, sign_bytes, verify_signature
from aiab.baked.bundle import (
    PLAN_FILE,
    bundle_files,
    certified_key,
    certified_keys,
    file_digest,
    key_bytes,
    load_bundle,
    make_certificate,
    restrictions,
    write_certificate,
)
from aiab.baked.plan_ir import PLAN_VERSION, PlanError, output_port

from server.config import Settings

PARTIES: tuple[str, ...] = ("lab", "auditor")
ARCHIVE_DIR = "_archive"  # agreements in the previous plan format, kept read-only

# Where the box's loader error codes point. One code at a time: the loader stops at the
# first problem, exactly as the box does at boot.
ISSUE_WHERE = {
    "invalid_bundle": "plan.json is missing, a symlink or larger than 256 KiB",
    "invalid_json": "plan.json is not valid JSON",
    "invalid_plan": "the plan graph (the builder lists the problems)",
    "invalid_trust_roots": "the trust file",
    "invalid_scope": "the Input Logs scope: the window ends before it starts",
    "invalid_output_schema": "the Final Report fields",
    "invalid_signature": "a signature file",
}
NODE_LABELS = {
    "input_logs": "Input Logs",
    "classifier": "Classifier",
    "run_python": "Run Python",
    "run_llm": "Run LLM",
    "final_report": "Final Report",
}


@dataclass(frozen=True)
class NodeView:
    id: str
    type: str
    label: str
    title: str
    inputs: tuple[str, ...]  # "name ← node.output"
    output: str  # "name · Type", or "releases N fields" for the report


@dataclass(frozen=True)
class PlanView:
    plan_dir: Path
    plan_id: str
    title: str
    description: str
    files: tuple[str, ...]
    digest: str
    plan: Any  # the plan document as saved, or None when plan.json is not JSON
    nodes: tuple[NodeView, ...]
    issue: str | None
    issue_where: str | None
    issues: tuple[dict[str, Any], ...]  # the loader's reasons, each with node and message
    signatures: dict[str, dict[str, Any]]
    frozen: bool
    stale: tuple[str, ...]
    public_keys: dict[str, str]
    state: str
    state_badge: str
    restrictions: dict[str, Any]
    history: tuple[dict[str, str], ...]

    @property
    def signed_by_both(self) -> bool:
        return all(s["status"] == "ok" for s in self.signatures.values())


def load_trust(settings: Settings) -> dict[str, Any]:
    """The keys and pins signatures are checked against: production's trust file as GitHub
    last served it, the same file the plan check and the next release use. This server's
    copy of the repository stands in when GitHub has not answered yet, or always when
    AIAB_TRUST_FILE names a dev trust file."""
    if settings.trust_follows_production:
        from server import deploystatus

        trust = deploystatus.production_trust_cached(settings)
        if trust is not None:
            return trust
    return decode(settings.trust_file.read_bytes())


def agreement_state(
    signatures: dict[str, dict[str, Any]], frozen: bool, issue: str | None
) -> tuple[str, str]:
    """The state is about signatures only: not signed, partly signed, expired, signed."""
    ok = [p for p, s in signatures.items() if s["status"] == "ok"]
    expired = [p for p, s in signatures.items() if s["status"] == "invalid"]
    if len(ok) == len(PARTIES):
        return "signed by both", "badge--ok"
    if ok and expired:
        return f"signed by {ok[0]}; {expired[0]}'s signature expired", "badge"
    if ok:
        return f"signed by {ok[0]}, waiting for the other party", "badge--ok"
    if expired:
        return "signatures expired, sign this version again", "badge"
    return "not signed", "badge"


def node_views(plan: Any) -> tuple[NodeView, ...]:
    """A one-line summary per node for the plan page, tolerant of a plan the loader refused."""
    if not isinstance(plan, dict) or not isinstance(plan.get("nodes"), list):
        return ()
    out = []
    for n in plan["nodes"]:
        if not isinstance(n, dict) or not isinstance(n.get("id"), str):
            continue
        kind = n.get("type") if n.get("type") in NODE_LABELS else "?"
        inputs = n.get("inputs") if isinstance(n.get("inputs"), dict) else {}
        try:
            port = output_port(n) if kind in NODE_LABELS else None
        except (KeyError, TypeError):
            port = None
        if kind == "final_report":
            fields = (n.get("config") or {}).get("fields") or []
            output = f"releases {len(fields)} field{'s' if len(fields) != 1 else ''}"
        else:
            output = f"{port[0]} · {port[1]}" if port else "?"
        out.append(
            NodeView(
                id=n["id"],
                type=kind,
                label=NODE_LABELS.get(kind, kind),
                title=str(n.get("title") or n["id"]),
                inputs=tuple(f"{k} ← {v}" for k, v in inputs.items()),
                output=output,
            )
        )
    return tuple(out)


def load_plan_view(settings: Settings) -> PlanView:
    plan_dir = settings.plan_dir
    files = bundle_files(plan_dir)
    plan_digest = file_digest(files)
    trust = load_trust(settings)
    try:
        plan = decode(files[PLAN_FILE])
    except AuditError:
        plan = None
    issue, issues = None, ()
    try:
        load_bundle(plan_dir, trust, signed=False)
    except PlanError as exc:
        issue, issues = exc.code, tuple(exc.issues)
    except AuditError as exc:
        issue = exc.code

    signatures: dict[str, dict[str, Any]] = {}
    for role in PARTIES:
        path = plan_dir / "signatures" / f"{role}.sig"
        if not path.is_file():
            signatures[role] = {"present": False, "status": "missing", "detail": "not uploaded"}
            continue
        text = path.read_text(encoding="utf-8").strip()
        certificate, signing_key = read_certificate(plan_dir / "signatures" / f"{role}.cert")
        try:
            if certificate is None:
                raise AuditError("invalid_certificate")
            signing_key = certified_key(certificate, role, trust)
            verify_signature(signing_key, text, "plan-bundle", {"plan_digest": plan_digest})
            entry = {"status": "ok", "detail": "verifies for the current plan digest"}
        except (AuditError, ValueError, KeyError):
            entry = {
                "status": "invalid",
                "detail": "does not verify for the current digest with a key the "
                f"{role} root certifies (stale edit, wrong key or missing certificate)",
            }
        signatures[role] = {
            "present": True,
            "signature": text,
            "signing_key": signing_key,
            **entry,
        }
    # The box refuses one signing key for both parties (certified_keys); so does this view.
    if all(s["status"] == "ok" for s in signatures.values()) and len(
        {key_bytes(s["signing_key"]) for s in signatures.values()}
    ) < len(PARTIES):
        for entry in signatures.values():
            entry["status"], entry["detail"] = "invalid", "the same signing key for both parties"
    # With a lab key on the server, the lab signature is the server's own and never
    # freezes the plan: the server signs the next version again. Only the auditor's does.
    auto = lab_auto_key(settings) is not None
    signatures["lab"]["auto"] = auto and signatures["lab"]["status"] == "ok"
    frozen = any(
        s["status"] == "ok" and not (auto and role == "lab") for role, s in signatures.items()
    )
    stale = [r for r, s in signatures.items() if s["status"] == "invalid"]
    state, badge = agreement_state(signatures, frozen, issue)
    where = ISSUE_WHERE.get(issue, issue) if issue else None
    meta = plan if isinstance(plan, dict) else {}
    return PlanView(
        plan_dir=plan_dir,
        plan_id=str(meta.get("plan_id") or plan_dir.name),
        title=str(meta.get("title") or ""),
        description=str(meta.get("description") or ""),
        files=tuple(sorted(files)),
        digest=plan_digest,
        plan=plan,
        nodes=node_views(plan),
        issue=issue,
        issue_where=where,
        issues=issues,
        signatures=signatures,
        frozen=frozen,
        stale=tuple(stale),
        public_keys={r: str(trust.get(f"{r}_key", "")) for r in PARTIES},
        state=state,
        state_badge=badge,
        restrictions=restrictions(trust),
        history=tuple(load_history(plan_dir)),
    )


# --------------------------------------------------------------------------- history

HISTORY_FILE = "history.jsonl"  # outside the digest: a trail for the parties, not evidence


def record_change(
    settings: Settings, actor: dict[str, str], kind: str, where: str, detail: str
) -> None:
    from datetime import UTC, datetime

    entry = {
        "at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "party": actor.get("party", "?"),
        "user": actor.get("username", "?"),
        "kind": kind,
        "where": where,
        "detail": detail[:4000],
    }
    with (settings.plan_dir / HISTORY_FILE).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_history(plan_dir: Path) -> list[dict[str, str]]:
    """Most recent first."""
    path = plan_dir / HISTORY_FILE
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries[::-1]


def last_change(history: list[dict[str, str]], where: str) -> dict[str, str] | None:
    return next((e for e in history if e.get("where") == where), None)


def _short_diff(before: str, after: str) -> str:
    import difflib

    lines = difflib.unified_diff(
        before.splitlines(), after.splitlines(), lineterm="", n=1, fromfile="before", tofile="after"
    )
    return "\n".join(list(lines)[2:])  # drop the file headers


def pretty(raw: bytes | str) -> str:
    """plan.json as people read it: indented, keys in the order the plan has them."""
    try:
        return json.dumps(json.loads(raw), indent=2, ensure_ascii=False) + "\n"
    except (ValueError, TypeError):
        return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw


# --------------------------------------------------------------------------- pre-save check


def box_would_reject(settings: Settings, overrides: dict[str, bytes]) -> str | None:
    """Run the box's loader on a copy of the folder with these files replaced. Returns one
    line naming the problem, or None when the box would accept the result."""
    code, issues = loader_verdict(settings, overrides)
    if code is None:
        return None
    detail = "; ".join(i["message"] for i in issues[:3]) or ISSUE_WHERE.get(code, code)
    return f"the box would reject this ({code}): {detail}"


def loader_verdict(
    settings: Settings, overrides: dict[str, bytes]
) -> tuple[str | None, list[dict[str, Any]]]:
    """(error code, the loader's issues) for the folder with ``overrides`` applied."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="aiab-check-") as tmp:
        # The bundle is one file: the loader reads nothing else, so nothing else is copied.
        folder = Path(tmp) / "plan"
        folder.mkdir()
        current = settings.plan_dir / PLAN_FILE
        files = {PLAN_FILE: current.read_bytes()} if current.is_file() else {}
        files.update(overrides)
        for name, raw in files.items():
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(raw)
        try:
            load_bundle(folder, load_trust(settings), signed=False)
        except PlanError as exc:
            return exc.code, list(exc.issues)
        except AuditError as exc:
            return exc.code, [{"message": ISSUE_WHERE.get(exc.code, exc.code), "node": None}]
    return None, []


# --------------------------------------------------------------------------- edits


def save_plan(
    settings: Settings, plan: Any, actor: dict[str, str]
) -> tuple[str | None, list[dict[str, Any]]]:
    """Write the plan the builder sent, after the box's loader accepted it.

    Returns (new digest, []) on success or (None, issues) when the box would refuse it.
    The file is written in the box's canonical JSON; the plan id stays the folder's."""
    if not isinstance(plan, dict):
        return None, [{"message": "the plan must be a JSON object", "node": None}]
    if plan.get("plan_id") != settings.plan_dir.name:
        return None, [
            {
                "message": f"plan_id must stay {settings.plan_dir.name}; rename the folder instead",
                "node": None,
                "path": ["plan_id"],
            }
        ]
    try:
        raw = canonical(plan) + b"\n"
    except AuditError:
        return None, [{"message": "the plan is not representable as JSON", "node": None}]
    code, issues = loader_verdict(settings, {PLAN_FILE: raw})
    if code is not None:
        return None, issues
    path = settings.plan_dir / PLAN_FILE
    before = path.read_bytes() if path.is_file() else b""
    if before != raw:
        path.write_bytes(raw)
        record_change(settings, actor, "plan", "plan", _short_diff(pretty(before), pretty(raw)))
    ensure_lab_signature(settings, actor)
    return file_digest({PLAN_FILE: raw}), []


def read_certificate(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """A stored certificate and the key it names, or (None, None) when there is none or it
    is not JSON. Whether the root signed it is the verifier's question, not this one."""
    if not path.is_file() or path.stat().st_size > 1024:
        return None, None
    try:
        certificate = decode(path.read_bytes())
    except AuditError:
        return None, None
    key = certificate.get("signing_key") if isinstance(certificate, dict) else None
    return certificate, key if isinstance(key, str) else None


def parse_certificate(text: str) -> dict[str, Any] | None:
    """The certificate as pasted or sent by the browser: one JSON object."""
    text = text.strip()
    if not text or len(text) > 1024:
        return None
    try:
        certificate = decode(text.encode("utf-8"))
    except (AuditError, UnicodeEncodeError):
        return None
    return certificate if isinstance(certificate, dict) else None


def write_signature(
    settings: Settings, role: str, content: bytes, actor: dict[str, str], certificate: str = ""
) -> tuple[str, str]:
    """Verify an uploaded signature against the key the party's root certifies, then store
    the signature and the certificate together (``<role>.sig`` and ``<role>.cert``)."""
    try:
        # Joined, not just stripped: Linux base64 wraps long output at 76 columns.
        text = "".join(content.decode("ascii").split())
    except UnicodeDecodeError:
        return "invalid", "that is not a base64 signature"
    if not text or len(text) > 256:
        return "invalid", "expected the base64 line the sign command printed"
    parsed = parse_certificate(certificate)
    if parsed is None:
        return "invalid", (
            "no certificate: paste the JSON line your root made for this signing key"
            " (sign_digest.py certify)"
        )
    plan_digest = file_digest(bundle_files(settings.plan_dir))
    trust = load_trust(settings)
    try:
        signing_key = certified_key(parsed, role, trust)
    except (AuditError, ValueError, KeyError):
        return "invalid", (
            f"the certificate is not one the {role} root production trusts made for this"
            " role, or it names the root itself"
        )
    try:
        verify_signature(signing_key, text, "plan-bundle", {"plan_digest": plan_digest})
    except (AuditError, ValueError, KeyError):
        return "invalid", "does not verify for the current plan digest with the certified key"
    for other in PARTIES:
        if other == role:
            continue
        stored, _ = read_certificate(settings.plan_dir / "signatures" / f"{other}.cert")
        if stored is None:
            continue
        try:
            certified_keys({role: parsed, other: stored}, trust)
        except (AuditError, KeyError):
            # Only a valid certificate of the other party can collide; an invalid one is
            # that party's problem and shows on its card.
            try:
                certified_key(stored, other, trust)
            except (AuditError, KeyError):
                continue
            return (
                "invalid",
                f"this signing key is already the {other}'s; the parties' keys must differ",
            )
    target = settings.plan_dir / "signatures" / f"{role}.sig"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text + "\n", encoding="utf-8")
    write_certificate(target.with_suffix(".cert"), parsed)
    record_change(
        settings,
        actor,
        "signature",
        f"signature: {role}",
        f"signed version {plan_digest[7:19]} with certified key {signing_key[:12]}…",
    )
    return "ok", "verified against the certified key and stored"


def _unlink_signature(folder: Path, role: str) -> bool:
    """Both files of a party's signature; True when the signature itself was there."""
    path = folder / "signatures" / f"{role}.sig"
    present = path.is_file()
    for file in (path, path.with_suffix(".cert")):
        if file.is_file():
            file.unlink()
    return present


def remove_signature(settings: Settings, role: str, actor: dict[str, str]) -> bool:
    """Drop one party's signature so that party can sign this same version again."""
    if not _unlink_signature(settings.plan_dir, role):
        return False
    record_change(settings, actor, "signature", f"signature: {role}", "removed to sign again")
    return True


def delete_signatures(settings: Settings, actor: dict[str, str] | None = None) -> int:
    removed = 0
    for role in PARTIES:
        if _unlink_signature(settings.plan_dir, role):
            removed += 1
    if removed and actor:
        record_change(
            settings, actor, "signature", "signatures", f"removed {removed} signature file(s)"
        )
    return removed


AUTO_SIGNER = {"party": "lab", "username": "server"}  # the change log's name for auto-signing

# Prototype shortcut: the server may hold the lab's certified signing key and sign every
# version as the lab (AIAB_LAB_KEY), and the auditor's root and certify each auditor's
# browser key (AIAB_AUDITOR_ROOT_KEY). Whoever controls the server can then sign as both
# parties. THREAT_MODEL.md, "Prototype shortcut", lists what changes for the real version.
SERVER_KEY_ENV = {"lab": ("AIAB_LAB_KEY", "AIAB_LAB_CERTIFICATE")}
ROOT_KEY_ENV = {"auditor": "AIAB_AUDITOR_ROOT_KEY"}


def _server_secrets(settings: Settings, party: str) -> tuple[str | None, str | None]:
    if party == "lab":
        return settings.lab_signing_key, settings.lab_signing_certificate
    return None, None


def server_key(settings: Settings, party: str) -> Any | None:
    """The private key the server signs with as `party`, or None when its variable is unset
    or not a key."""
    return _private_key(_server_secrets(settings, party)[0])


def server_public_key(settings: Settings, party: str) -> str | None:
    key = server_key(settings, party)
    if key is None:
        return None
    return base64.b64encode(key.public_key().public_bytes_raw()).decode()


def server_certificate(settings: Settings, party: str) -> dict[str, Any] | None:
    """The certificate the party's root gave the server's signing key, or None when unset
    or not JSON."""
    _, text = _server_secrets(settings, party)
    if not text:
        return None
    return parse_certificate(text)


def server_problem(settings: Settings, party: str) -> str | None:
    """Why the server cannot sign as `party` right now: a malformed key, no certificate, or
    a certificate the party's root on production did not make for this key. None when it
    can, and None when it holds no key for the party (nothing to fix)."""
    raw, _ = _server_secrets(settings, party)
    if not raw:
        return None
    key_env, cert_env = SERVER_KEY_ENV[party]
    public = server_public_key(settings, party)
    if public is None:
        return f"{key_env} is not a base64 Ed25519 private key"
    certificate = server_certificate(settings, party)
    if certificate is None:
        return f"{cert_env} is not set, or is not the JSON line sign_digest.py certify printed"
    try:
        certified = certified_key(certificate, party, load_trust(settings))
    except (AuditError, KeyError):
        return f"{cert_env} was not made by the {party} root production trusts"
    if certified != public:
        return f"{cert_env} certifies another key, not the server's {party} key ({public})"
    return None


def server_signs(settings: Settings, party: str) -> bool:
    """The server holds a working signing key for `party`."""
    return server_key(settings, party) is not None and server_problem(settings, party) is None


def _server_signature_current(settings: Settings, party: str, plan_digest: str) -> bool:
    """The stored signature is the server's, for this version, under the current root."""
    target = settings.plan_dir / "signatures" / f"{party}.sig"
    if not target.is_file():
        return False
    try:
        stored, _ = read_certificate(target.with_suffix(".cert"))
        # The stored certificate must be the current root's, for this key: after a root
        # rotation the signature is re-made with the new certificate.
        public = server_public_key(settings, party)
        if stored is None or certified_key(stored, party, load_trust(settings)) != public:
            raise AuditError("invalid_certificate")
        verify_signature(
            public,
            target.read_text(encoding="utf-8").strip(),
            "plan-bundle",
            {"plan_digest": plan_digest},
        )
        return True
    except (AuditError, ValueError, KeyError):
        return False


def _write_server_signature(settings: Settings, party: str, plan_digest: str) -> None:
    key = server_key(settings, party)
    signature = base64.b64encode(
        key.sign(sign_bytes("plan-bundle", {"plan_digest": plan_digest}))
    ).decode()
    target = settings.plan_dir / "signatures" / f"{party}.sig"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(signature + "\n", encoding="utf-8")
    write_certificate(target.with_suffix(".cert"), server_certificate(settings, party))


def _private_key(raw: str | None) -> Any | None:
    if not raw:
        return None
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    try:
        return Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw, validate=True))
    except (ValueError, TypeError):
        return None


def issuer_root(settings: Settings, party: str) -> Any | None:
    """The party's root private key, when this server holds it to certify browser keys."""
    return _private_key(settings.auditor_root_key if party == "auditor" else None)


def issuer_problem(settings: Settings, party: str) -> str | None:
    """Why the server cannot certify keys for `party`: a malformed root, or not the root
    production trusts. None when it can, or when it holds no root for the party."""
    raw = settings.auditor_root_key if party == "auditor" else None
    if not raw:
        return None
    root = issuer_root(settings, party)
    if root is None:
        return f"{ROOT_KEY_ENV[party]} is not a base64 Ed25519 private key"
    public = root.public_key().public_bytes_raw()
    try:
        trusted = key_bytes(load_trust(settings).get(f"{party}_key", ""))
    except AuditError:
        trusted = b""
    if public != trusted:
        shown = base64.b64encode(public).decode()
        return f"{ROOT_KEY_ENV[party]} ({shown[:8]}…) is not the {party} root production trusts"
    return None


def server_certifies(settings: Settings, party: str) -> bool:
    """The server holds the party's trusted root and certifies its members' browser keys."""
    return issuer_root(settings, party) is not None and issuer_problem(settings, party) is None


def issued_log(settings: Settings) -> Path:
    """Every certificate the server made, one JSON line each, next to the accounts."""
    return settings.accounts_file.parent / "issued-certificates.jsonl"


def issue_certificate(
    settings: Settings, party: str, signing_key: str, actor: dict[str, str]
) -> tuple[str, Any]:
    """Certify the public key a logged-in member's browser made. Returns ("ok", the
    certificate) or ("error", why). Refuses a root, the lab's server key and anything that
    is not a canonical 32-byte key; records every certificate it makes."""
    if not server_certifies(settings, party):
        return "error", issuer_problem(settings, party) or f"this server holds no {party} root"
    try:
        raw = key_bytes(signing_key)
    except AuditError:
        return "error", "not an Ed25519 public key (base64 of 32 bytes)"
    trust = load_trust(settings)
    taken = set()
    for role in PARTIES:
        try:
            taken.add(key_bytes(trust.get(f"{role}_key", "")))
        except AuditError:
            pass
    lab = server_public_key(settings, "lab")
    if lab:
        taken.add(base64.b64decode(lab))
    if raw in taken:
        return "error", "that key is a root or the lab's key"
    certificate = make_certificate(issuer_root(settings, party), party, signing_key)
    if certified_key(certificate, party, trust) != signing_key:  # what the box will check
        return "error", "the certificate does not verify against the trusted root"
    from datetime import UTC, datetime

    record = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "party": party,
        # The login as the session carries it: for WorkOS the username is the email's
        # local part, so two organizations' "alex" look alike here.
        "username": actor.get("username", ""),
        "display_name": actor.get("display_name", ""),
        "signing_key": signing_key,
    }
    log = issued_log(settings)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as out:
        out.write(json.dumps(record, sort_keys=True) + "\n")
    return "ok", certificate


# The lab's key signs by itself; these names are the lab's view of the functions above.
def lab_auto_key(settings: Settings) -> Any | None:
    return server_key(settings, "lab")


def lab_auto_public_key(settings: Settings) -> str | None:
    return server_public_key(settings, "lab")


def lab_auto_certificate(settings: Settings) -> dict[str, Any] | None:
    return server_certificate(settings, "lab")


def lab_auto_problem(settings: Settings) -> str | None:
    return server_problem(settings, "lab")


def ensure_lab_signature(settings: Settings, actor: dict[str, str] | None = None) -> bool:
    """Sign the current version as the lab with the server's key, unless there is no key, it
    is not the trusted one, or a valid lab signature is already there. True when it signed."""
    if not server_signs(settings, "lab"):
        return False
    plan_digest = file_digest(bundle_files(settings.plan_dir))
    if _server_signature_current(settings, "lab", plan_digest):
        return False
    _write_server_signature(settings, "lab", plan_digest)
    who = f" after a change by {actor['username']}" if actor and actor.get("username") else ""
    record_change(
        settings,
        AUTO_SIGNER,
        "signature",
        "signature: lab",
        f"signed version {plan_digest[7:19]} automatically with the server's lab key{who}",
    )
    return True


ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"  # no leading _ or .: those folders are ours


def for_agreement(settings: Settings, agreement_id: str) -> Settings | None:
    """Settings pointing at one agreement folder under agreements/."""
    from dataclasses import replace

    if not re.fullmatch(ID_PATTERN, agreement_id):
        return None
    folder = settings.agreements_dir / agreement_id
    if not folder.is_dir():
        return None
    return replace(settings, plan_dir=folder)


def is_current_format(folder: Path) -> bool:
    """A folder holds a plan in the format this UI and the box read."""
    path = folder / PLAN_FILE
    if not path.is_file():
        return False
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("version") == PLAN_VERSION
    except (OSError, ValueError, AttributeError):
        return False


def archive_old_agreements(settings: Settings) -> list[str]:
    """Move agreements written for the previous plan format (plan.py and a plan.json of
    schema aiab-baked-plan/v1) into agreements/_archive/. The box no longer runs that
    format, so they cannot be edited, signed or run; they stay on disk, read only,
    until someone deletes them. Returns the names moved."""
    root = settings.agreements_dir
    if not root.is_dir():
        return []
    moved = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or folder.name.startswith((".", "_")) or is_current_format(folder):
            continue
        target = root / ARCHIVE_DIR / folder.name
        target.parent.mkdir(exist_ok=True)
        if target.exists():  # archived once already under this name: keep both
            target = root / ARCHIVE_DIR / f"{folder.name}-{folder_digest(folder)[7:15]}"
        shutil.move(str(folder), str(target))
        moved.append(folder.name)
    return moved


def folder_digest(folder: Path) -> str:
    """A digest of a folder's files, for naming an archived copy."""
    import hashlib

    hashed = hashlib.sha256()
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        hashed.update(path.relative_to(folder).as_posix().encode() + path.read_bytes())
    return "sha256:" + hashed.hexdigest()


def list_archived(settings: Settings) -> list[dict[str, Any]]:
    """The archived agreements, read only: name, what the old plan.json called itself, size."""
    root = settings.agreements_dir / ARCHIVE_DIR
    if not root.is_dir():
        return []
    rows = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        plan_id, schema = folder.name, "?"
        try:
            old = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
            plan_id, schema = str(old.get("plan_id", plan_id)), str(old.get("schema", "?"))
        except (OSError, ValueError, AttributeError):
            pass
        rows.append(
            {
                "id": folder.name,
                "plan_id": plan_id,
                "schema": schema,
                "files": sum(1 for p in folder.rglob("*") if p.is_file()),
            }
        )
    return rows


def delete_archived(settings: Settings, name: str) -> str | None:
    if not re.fullmatch(ID_PATTERN, name):
        return "unknown archived agreement"
    folder = settings.agreements_dir / ARCHIVE_DIR / name
    if not folder.is_dir():
        return "unknown archived agreement"
    shutil.rmtree(folder)
    return None


def list_agreements(settings: Settings) -> list[dict[str, Any]]:
    """Every folder under agreements/: what people edit, sign and run. Folders in the
    previous format are archived first."""
    archive_old_agreements(settings)
    rows = []
    candidates = []
    if settings.agreements_dir.is_dir():
        candidates += [
            (f.name, f)
            for f in sorted(settings.agreements_dir.iterdir())
            if f.is_dir() and not f.name.startswith((".", "_"))
        ]
    for agreement_id, _folder in candidates:
        try:
            view = load_plan_view(for_agreement(settings, agreement_id) or settings)
            rows.append(
                {
                    "id": agreement_id,
                    "plan_id": view.plan_id,
                    "title": view.title,
                    "digest": view.digest,
                    "state": view.state,
                    "signed": view.signed_by_both,
                    "signatures": {r: s["status"] for r, s in view.signatures.items()},
                    "public_keys": view.public_keys,
                    "error": None,
                    "issue": view.issue,
                }
            )
        except Exception as exc:  # noqa: BLE001 - one broken folder must not hide the others
            rows.append(
                {
                    "id": agreement_id,
                    "plan_id": agreement_id,
                    "title": "",
                    "digest": None,
                    "state": f"does not load: {type(exc).__name__}",
                    "signed": False,
                    "signatures": {},
                    "public_keys": {},
                    "error": str(exc),
                    "issue": None,
                }
            )
    return rows


def _copy_folder(source: Path, target: Path) -> None:
    shutil.copytree(
        source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"), dirs_exist_ok=True
    )


def taken_names(settings: Settings) -> list[str]:
    """Names a new agreement cannot take: existing folders under agreements/ (archived or
    not). The form checks these as the user types."""
    names = set()
    if settings.agreements_dir.is_dir():
        names |= {f.name for f in settings.agreements_dir.iterdir()}
    return sorted(names)


def new_agreement(
    settings: Settings, form: dict[str, str], actor: dict[str, str]
) -> tuple[list[str], str | None]:
    """Create agreements/<name>/ from the minimal template, or as a copy of another
    agreement's plan (``source=copy``, ``copy_from=<id>``: the plan only, no signatures,
    no history).

    From the template, the question becomes the plan's description and the classifier's
    instructions. A copy keeps the plan as it was, with the new name and the question as
    its description. Returns (errors, agreement id).
    """
    errors: list[str] = []
    plan_id = form.get("plan_id", "").strip()
    copying = form.get("source") == "copy"
    origin = for_agreement(settings, form.get("copy_from", "").strip()) if copying else None
    if copying and origin is None:
        errors.append("copy from: pick an existing agreement")
    if not re.fullmatch(ID_PATTERN, plan_id):
        errors.append(
            "name: letters, digits, _ and - only, up to 128 characters,"
            " starting with a letter or digit"
        )
    elif (settings.agreements_dir / plan_id).exists():
        errors.append(f"name: an agreement called {plan_id} already exists")
    problem = form.get("problem", "").strip().replace("\r\n", "\n")
    if not 1 <= len(problem) <= 16384:
        errors.append("question: required, up to 16384 characters")
    if errors:
        return errors, None

    target = settings.agreements_dir / plan_id
    _copy_folder(Path(__file__).with_name("template"), target)
    shutil.rmtree(target / "signatures", ignore_errors=True)
    path = target / PLAN_FILE
    if origin is not None:
        shutil.copyfile(origin.plan_dir / PLAN_FILE, path)
    plan = decode(path.read_bytes())
    plan["plan_id"] = plan_id
    plan["description"] = problem
    plan["title"] = problem.splitlines()[0][:120]
    if origin is None:
        for node in plan["nodes"]:
            if node["type"] == "classifier":
                node["config"]["instructions"] = problem + "\n"
    path.write_bytes(canonical(plan) + b"\n")
    history = target / HISTORY_FILE
    if history.is_file():
        history.unlink()  # a new agreement starts its own trail
    created = for_agreement(settings, plan_id) or settings
    what = (
        f"copied from {origin.plan_dir.name}" if origin is not None else "created from the template"
    )
    record_change(created, actor, "created", "agreement", what)
    ensure_lab_signature(created, actor)
    return [], plan_id


def delete_agreement(settings: Settings, agreement_id: str) -> str | None:
    """Remove an agreement folder. Runs keep their own frozen copy of what they sent."""
    target = for_agreement(settings, agreement_id)
    if target is None:
        return "unknown agreement"
    shutil.rmtree(target.plan_dir)
    return None


SSH_ED25519_HEADER = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20"


def normalize_public_key(text: str) -> tuple[str | None, str | None]:
    """The 44-character base64 form of an Ed25519 public key (what trust.json holds), from
    either that form or an OpenSSH line, "ssh-ed25519 AAAA... comment", as 1Password and
    ssh-keygen print it. Returns (key, None) or (None, why it is neither). The trust file
    itself is never written by the UI: a key changes through a pull request."""
    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    parts = text.split()
    if len(parts) >= 2 and parts[0] == "ssh-ed25519":
        try:
            blob = base64.b64decode(parts[1], validate=True)
        except ValueError:
            blob = b""
        if len(blob) != 51 or not blob.startswith(SSH_ED25519_HEADER):
            return None, "that ssh-ed25519 line is not complete; copy the whole public key"
        raw = blob[len(SSH_ED25519_HEADER) :]
    elif len(parts) == 1:
        try:
            raw = base64.b64decode(parts[0], validate=True)
        except ValueError:
            raw = b""
    else:
        return None, "paste one key: the 44-character line ending in =, or the ssh-ed25519 line"
    try:
        Ed25519PublicKey.from_public_bytes(raw)
    except Exception:  # noqa: BLE001 - any decoding or key error means "not a key"
        return None, (
            "that is not an Ed25519 public key: expected the 44-character line ending in =,"
            " or the ssh-ed25519 line from 1Password"
        )
    return base64.b64encode(raw).decode(), None


def check_public_key(public_key: str) -> str | None:
    """Why ``public_key`` is not an Ed25519 public key in either accepted form, or None."""
    return normalize_public_key(public_key)[1]
