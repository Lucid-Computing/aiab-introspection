"""View models over an agreement folder, built on the box's own loader (aiab.baked.bundle).

An agreement is one file, plan.json, the audit plan as a graph. The builder (ui/playground,
embedded in the plan page) edits it and saves it through ``save_plan``, which runs the
box's loader on the candidate first, so nothing the box would refuse is ever written.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiab.audit.protocol import AuditError, canonical, decode, verify_signature
from aiab.baked.bundle import PLAN_FILE, bundle_files, file_digest, load_bundle, restrictions
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
        try:
            verify_signature(
                trust[f"{role}_key"], text, "plan-bundle", {"plan_digest": plan_digest}
            )
            entry = {"status": "ok", "detail": "verifies for the current plan digest"}
        except (AuditError, ValueError, KeyError):
            entry = {
                "status": "invalid",
                "detail": "does not verify for the current digest (stale edit or wrong key)",
            }
        signatures[role] = {"present": True, "signature": text, **entry}
    frozen = any(s["status"] == "ok" for s in signatures.values())
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
    if before == raw:
        return file_digest({PLAN_FILE: raw}), []
    path.write_bytes(raw)
    record_change(settings, actor, "plan", "plan", _short_diff(pretty(before), pretty(raw)))
    return file_digest({PLAN_FILE: raw}), []


def write_signature(
    settings: Settings, role: str, content: bytes, actor: dict[str, str]
) -> tuple[str, str]:
    """Verify an uploaded signature file against the pinned key, then store it."""
    try:
        # Joined, not just stripped: Linux base64 wraps long output at 76 columns.
        text = "".join(content.decode("ascii").split())
    except UnicodeDecodeError:
        return "invalid", "that is not a base64 signature"
    if not text or len(text) > 256:
        return "invalid", "expected the base64 line the sign command printed"
    plan_digest = file_digest(bundle_files(settings.plan_dir))
    trust = load_trust(settings)
    try:
        verify_signature(trust[f"{role}_key"], text, "plan-bundle", {"plan_digest": plan_digest})
    except (AuditError, ValueError, KeyError):
        return "invalid", "does not verify for the current plan digest with the pinned key"
    target = settings.plan_dir / "signatures" / f"{role}.sig"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text + "\n", encoding="utf-8")
    record_change(
        settings, actor, "signature", f"signature: {role}", f"signed version {plan_digest[7:19]}"
    )
    return "ok", "verified against the pinned key and stored"


def remove_signature(settings: Settings, role: str, actor: dict[str, str]) -> bool:
    """Drop one party's signature so that party can sign this same version again."""
    path = settings.plan_dir / "signatures" / f"{role}.sig"
    if not path.exists():
        return False
    path.unlink()
    record_change(settings, actor, "signature", f"signature: {role}", "removed to sign again")
    return True


def delete_signatures(settings: Settings, actor: dict[str, str] | None = None) -> int:
    removed = 0
    for role in PARTIES:
        path = settings.plan_dir / "signatures" / f"{role}.sig"
        if path.is_file():
            path.unlink()
            removed += 1
    if removed and actor:
        record_change(
            settings, actor, "signature", "signatures", f"removed {removed} signature file(s)"
        )
    return removed


ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"  # no leading _ or .: those folders are ours


def for_agreement(settings: Settings, agreement_id: str) -> Settings | None:
    """Settings pointing at one agreement folder; "plan" is the deploy slot plan/."""
    from dataclasses import replace

    if agreement_id == "plan":
        return settings
    if not re.fullmatch(ID_PATTERN, agreement_id):
        return None
    folder = settings.agreements_dir / agreement_id
    if not folder.is_dir():
        return None
    return replace(settings, plan_dir=folder)


def deployed_plan(settings: Settings) -> dict[str, Any]:
    """The deploy slot, plan/: what was deployed, read only. Changes reach it only through a
    merged plan pull request and a pull of the checkout, never through this UI."""
    try:
        view = load_plan_view(settings)
    except Exception as exc:  # noqa: BLE001 - a broken deploy slot is shown, not raised
        return {
            "plan_id": "plan",
            "digest": None,
            "state": None,
            "error": str(exc),
            "trusted": False,
        }
    # Signed by the keys production trusts now? After a key change this turns false, and the
    # box's plan counts as invalid until a plan signed with the new keys is deployed.
    try:
        load_bundle(settings.plan_dir, load_trust(settings), signed=True)
        trusted = True
    except (OSError, ValueError, AuditError):
        trusted = False
    return {
        "plan_id": view.plan_id,
        "digest": view.digest,
        "state": view.state,
        "error": None,
        "trusted": trusted,
    }


FILE_CUT = 64 * 1024


def plan_files(settings: Settings) -> list[dict[str, Any]]:
    """Every file under the deploy slot, plan/, in path order, read only: what the box image
    was built from. Caches and hidden files are left out; a long file is cut so the page
    stays readable, and plan.json is shown indented."""
    root = settings.plan_dir
    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root)
        if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue
        raw = path.read_bytes()
        try:
            text = pretty(raw) if rel.name == PLAN_FILE else raw.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        files.append(
            {
                "path": str(rel),
                "size": len(raw),
                "text": text[:FILE_CUT] if text is not None else None,
                "cut": text is not None and len(text) > FILE_CUT,
            }
        )
    return files


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
    format, so they cannot be edited, signed or deployed; they stay on disk, read only,
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
    """Every folder under agreements/, the editable ones. The deploy slot is not an
    agreement; see deployed_plan(). Folders in the previous format are archived first."""
    archive_old_agreements(settings)
    rows = []
    try:
        selected = file_digest(bundle_files(settings.plan_dir))
    except AuditError:
        selected = None
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
                    "selected": view.digest == selected,  # same files as the deploy slot
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
                    "selected": False,
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
    not) and the deploy slot's. The form checks these as the user types."""
    names = {"plan"}
    if settings.agreements_dir.is_dir():
        names |= {f.name for f in settings.agreements_dir.iterdir()}
    return sorted(names)


def new_agreement(
    settings: Settings, form: dict[str, str], actor: dict[str, str]
) -> tuple[list[str], str | None]:
    """Create agreements/<name>/ from the minimal template or as a copy of the deploy slot.

    Signatures are never copied. The question becomes the plan's description and, for the
    template, the classifier's instructions. Returns (errors, agreement id).
    """
    errors: list[str] = []
    plan_id = form.get("plan_id", "").strip()
    if not re.fullmatch(ID_PATTERN, plan_id):
        errors.append(
            "name: letters, digits, _ and - only, up to 128 characters,"
            " starting with a letter or digit"
        )
    elif plan_id == "plan" or (settings.agreements_dir / plan_id).exists():
        errors.append(f"name: an agreement called {plan_id} already exists")
    problem = form.get("problem", "").strip().replace("\r\n", "\n")
    if not 1 <= len(problem) <= 16384:
        errors.append("question: required, up to 16384 characters")
    if form.get("source") == "copy" and not is_current_format(settings.plan_dir):
        errors.append(
            "start from: the deployed plan is in the previous format and cannot be copied;"
            " start from the minimal plan instead"
        )
    if errors:
        return errors, None

    target = settings.agreements_dir / plan_id
    if form.get("source") == "copy":
        _copy_folder(settings.plan_dir, target)
    else:
        _copy_folder(Path(__file__).with_name("template"), target)
    shutil.rmtree(target / "signatures", ignore_errors=True)
    path = target / PLAN_FILE
    plan = decode(path.read_bytes())
    plan["plan_id"] = plan_id
    plan["description"] = problem
    if form.get("source") != "copy":
        plan["title"] = problem.splitlines()[0][:120]
        for node in plan["nodes"]:
            if node["type"] == "classifier":
                node["config"]["instructions"] = problem + "\n"
    path.write_bytes(canonical(plan) + b"\n")
    history = target / HISTORY_FILE
    if history.is_file():
        history.unlink()  # a new agreement starts its own trail
    source = "a copy of the deployed plan" if form.get("source") == "copy" else "the template"
    record_change(
        for_agreement(settings, plan_id) or settings,
        actor,
        "created",
        "agreement",
        f"created from {source}",
    )
    return [], plan_id


def delete_agreement(settings: Settings, agreement_id: str) -> str | None:
    """Remove an agreement folder; never the deploy slot or the agreement it was made from."""
    if agreement_id == "plan":
        return "the deploy slot cannot be deleted"
    target = for_agreement(settings, agreement_id)
    if target is None:
        return "unknown agreement"
    shutil.rmtree(target.plan_dir)  # the deployed copy, if any, stays in plan/ and in git
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
