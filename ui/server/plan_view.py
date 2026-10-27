"""View models over the plan folder, built on the box's own loader (aiab.baked.bundle)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiab.audit.protocol import AuditError, canonical, decode, verify_signature
from aiab.baked.bundle import bundle_files, file_digest, load_bundle, restrictions

from server.config import Settings
from server.pipeline import PARTIES, PIPELINE, Step
from server.plansource import FunctionInfo, plan_functions, replace_function_source

# Where the box's loader error codes point. One code at a time: the loader stops at the
# first problem, exactly as the box does at boot.
ISSUE_WHERE = {
    "invalid_bundle": "folder layout (__init__.py, plan.py, plan.json, prompts/*.txt)",
    "invalid_bundle_init": "__init__.py",
    "invalid_plan": "plan.json does not match schema aiab-baked-plan/v1",
    "unapproved_parties": "plan.json: parties / input_scope.logger_key_id",
    "invalid_trust_roots": "the trust file",
    "unapproved_model": "plan.json: model does not match the pinned model",
    "invalid_limits": "plan.json: window exceeds limits.max_logs_per_query",
    "invalid_scope": "plan.json: input_scope window",
    "missing_prompt": "prompts/<prompt_id>.txt",
    "invalid_plan_source": "plan.py: functions, imports or forbidden names",
    "invalid_output_schema": "plan.json: output_schema",
}
ISSUE_STEP = {
    "unapproved_model": "classify",
    "missing_prompt": "classify",
    "invalid_limits": "build_query",
    "invalid_output_schema": "emit_report",
}


def get_setting(config: Any, dotted: str) -> Any:
    node = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def set_setting(config: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = config
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def parse_setting(text: str) -> Any:
    """JSON, but forgiving about a comma after the last item of a list or object."""
    import re

    try:
        return json.loads(text)
    except ValueError:
        return json.loads(re.sub(r",\s*([\]}])", r"\1", text))


def _kind(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return "null"


@dataclass(frozen=True)
class SettingView:
    path: str
    value: Any
    value_json: str
    kind: str

    @property
    def multiline(self) -> bool:
        long_text = self.kind == "str" and (len(self.value) > 60 or self.path.endswith(".problem"))
        return self.kind in {"list", "dict"} or long_text

    @property
    def raw(self) -> str:
        """What the form shows: plain text for strings, JSON for everything else."""
        return self.value if self.kind == "str" else self.value_json


@dataclass(frozen=True)
class StepView:
    step: Step
    function: FunctionInfo | None
    settings: tuple[SettingView, ...]
    issue: str | None
    prompt_id: str | None
    prompt_text: str | None


@dataclass(frozen=True)
class PlanView:
    plan_dir: Path
    plan_id: str
    files: tuple[str, ...]
    digest: str
    config: Any
    steps: tuple[StepView, ...]
    issue: str | None
    issue_where: str | None
    signatures: dict[str, dict[str, Any]]
    frozen: bool
    stale: tuple[str, ...]
    public_keys: dict[str, str]
    state: str
    state_badge: str
    restrictions: dict[str, Any]
    prompts: dict[str, str]
    history: tuple[dict[str, str], ...]

    @property
    def signed_by_both(self) -> bool:
        return all(s["status"] == "ok" for s in self.signatures.values())

    def step(self, name: str) -> StepView | None:
        return next((s for s in self.steps if s.step.name == name), None)


def load_trust(settings: Settings) -> dict[str, Any]:
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


def load_plan_view(settings: Settings) -> PlanView:
    plan_dir = settings.plan_dir
    files = bundle_files(plan_dir)
    plan_digest = file_digest(files)
    config = decode(files["plan.json"])
    trust = load_trust(settings)

    issue = None
    try:
        load_bundle(plan_dir, trust, signed=False)
    except AuditError as exc:
        issue = exc.code

    try:
        functions = plan_functions(files["plan.py"].decode("utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        functions = {}
    prompts = {
        Path(name).stem: raw.decode("utf-8", "replace")
        for name, raw in sorted(files.items())
        if name.startswith("prompts/")
    }

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

    prompt_id = get_setting(config, "classification.prompt_id")
    steps = []
    for step in PIPELINE:
        values = tuple(
            SettingView(
                path=path,
                value=get_setting(config, path),
                value_json=json.dumps(get_setting(config, path), indent=2),
                kind=_kind(get_setting(config, path)),
            )
            for path in step.settings
        )
        step_issue = issue if ISSUE_STEP.get(issue) == step.name else None
        if issue == "invalid_plan_source" and step.name not in functions and step.called:
            step_issue = issue
        steps.append(
            StepView(
                step=step,
                function=functions.get(step.name),
                settings=values,
                issue=step_issue,
                prompt_id=prompt_id if step.kind == "run_llm" else None,
                prompt_text=prompts.get(prompt_id) if step.kind == "run_llm" else None,
            )
        )
    state, badge = agreement_state(signatures, frozen, issue)
    where = ISSUE_WHERE.get(issue, issue) if issue else None
    return PlanView(
        plan_dir=plan_dir,
        plan_id=str(get_setting(config, "plan_id") or "?"),
        files=tuple(sorted(files)),
        digest=plan_digest,
        config=config,
        steps=tuple(steps),
        issue=issue,
        issue_where=where,
        signatures=signatures,
        frozen=frozen,
        stale=tuple(stale),
        public_keys={r: str(trust.get(f"{r}_key", "")) for r in PARTIES},
        state=state,
        state_badge=badge,
        restrictions=restrictions(trust),
        prompts=prompts,
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


# --------------------------------------------------------------------------- pre-save check


def _explain_schema(config: Any) -> str:
    """Name the field the box's plan schema rejects, when the jsonschema package can tell."""
    try:
        from aiab.baked.bundle import PLAN
        from jsonschema import Draft202012Validator

        errors = sorted(Draft202012Validator(PLAN).iter_errors(config), key=lambda e: e.json_path)
    except Exception:  # noqa: BLE001 - fall back to the bare code
        return ""
    if not errors:
        return ""
    error = errors[0]
    where = error.json_path.removeprefix("$.") or "plan.json"
    return f"{where}: {error.message}"


def box_would_reject(settings: Settings, overrides: dict[str, bytes]) -> str | None:
    """Run the box's loader on a copy of the folder with these files replaced."""
    import shutil
    import tempfile

    with tempfile.TemporaryDirectory(prefix="aiab-check-") as tmp:
        folder = Path(tmp) / "plan"
        shutil.copytree(
            settings.plan_dir,
            folder,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests", "signatures"),
        )
        for name, raw in overrides.items():
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(raw)
        try:
            load_bundle(folder, load_trust(settings), signed=False)
        except AuditError as exc:
            detail = ""
            if exc.code == "invalid_plan" and "plan.json" in overrides:
                try:
                    detail = _explain_schema(decode(overrides["plan.json"]))
                except AuditError:
                    detail = ""
            where = detail or ISSUE_WHERE.get(exc.code, exc.code)
            return f"the box would reject this ({exc.code}): {where}"
    return None


# --------------------------------------------------------------------------- edits


def save_step_settings(
    settings: Settings, step_name: str, form: dict[str, str], actor: dict[str, str]
) -> list[str]:
    step = next((s for s in PIPELINE if s.name == step_name), None)
    if step is None:
        return [f"unknown step {step_name}"]
    path = settings.plan_dir / "plan.json"
    config = decode(path.read_bytes())
    errors: list[str] = []
    changes: list[tuple[str, str, str]] = []
    numeric = {"int", "float"}
    for dotted in step.settings:
        if dotted not in form:
            continue
        current = get_setting(config, dotted)
        if _kind(current) == "str":
            value = form[dotted].replace("\r\n", "\n").strip()
        else:
            try:
                value = parse_setting(form[dotted])
            except ValueError as exc:
                errors.append(f"{dotted}: could not read this as a value ({exc})")
                continue
        if current is not None and _kind(current) != _kind(value):
            if not (_kind(current) in numeric and _kind(value) in numeric):
                errors.append(f"{dotted}: expected {_kind(current)}, got {_kind(value)}")
                continue
        if current != value:
            changes.append((dotted, json.dumps(current), json.dumps(value)))
        set_setting(config, dotted, value)
    if errors:
        return errors
    if not changes:
        return []
    rejected = box_would_reject(settings, {"plan.json": canonical(config) + b"\n"})
    if rejected:
        return [rejected]
    path.write_bytes(canonical(config) + b"\n")  # the box's canonical form
    for dotted, before, after in changes:
        record_change(settings, actor, "settings", f"{step_name}: {dotted}", f"{before} → {after}")
    return []


def save_function(
    settings: Settings, name: str, new_source: str, actor: dict[str, str]
) -> str | None:
    path = settings.plan_dir / "plan.py"
    before = path.read_text(encoding="utf-8")
    try:
        updated = replace_function_source(before, name, new_source.replace("\r\n", "\n"))
    except AuditError as exc:
        return f"the box would reject this plan.py: {exc.code}"
    except (SyntaxError, ValueError, KeyError) as exc:
        return f"{type(exc).__name__}: {exc}"
    if updated != before:
        path.write_text(updated, encoding="utf-8")
        record_change(settings, actor, "code", f"{name}: code", _short_diff(before, updated))
    return None


def save_prompt(settings: Settings, prompt_id: str, text: str, actor: dict[str, str]) -> str | None:
    path = settings.plan_dir / "prompts" / f"{prompt_id}.txt"
    if not path.is_file():
        return f"prompts/{prompt_id}.txt does not exist"
    text = text.replace("\r\n", "\n")
    if not text.endswith("\n"):
        text += "\n"
    before = path.read_text(encoding="utf-8")
    if text != before:
        rejected = box_would_reject(settings, {f"prompts/{prompt_id}.txt": text.encode("utf-8")})
        if rejected:
            return rejected
        path.write_text(text, encoding="utf-8")
        record_change(
            settings, actor, "prompt", f"classify: prompt {prompt_id}", _short_diff(before, text)
        )
    return None


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


PLACEHOLDER_DIGEST = "sha256:" + "0" * 64
ID_PATTERN = r"^[A-Za-z0-9_-]{1,128}$"


def for_agreement(settings: Settings, agreement_id: str) -> Settings | None:
    """Settings pointing at one agreement folder; "plan" is the deploy slot plan/."""
    import re
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
    stays readable."""
    root = settings.plan_dir
    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root)
        if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
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


def list_agreements(settings: Settings) -> list[dict[str, Any]]:
    """Every folder under agreements/, the editable ones. The deploy slot is not an
    agreement; see deployed_plan()."""
    rows = []
    try:
        selected = file_digest(bundle_files(settings.plan_dir))
    except AuditError:
        selected = None
    candidates = []
    if settings.agreements_dir.is_dir():
        candidates += [(f.name, f) for f in sorted(settings.agreements_dir.iterdir()) if f.is_dir()]
    for agreement_id, _folder in candidates:
        try:
            view = load_plan_view(for_agreement(settings, agreement_id) or settings)
            rows.append(
                {
                    "id": agreement_id,
                    "plan_id": view.plan_id,
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
    import shutil

    shutil.copytree(
        source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"), dirs_exist_ok=True
    )


def new_agreement(
    settings: Settings, form: dict[str, str], actor: dict[str, str]
) -> tuple[list[str], str | None]:
    """Create agreements/<name>/ from the minimal template or as a copy of the deploy slot.

    Signatures and the evaluation are never copied. Returns (errors, agreement id).
    """
    import re

    errors: list[str] = []
    plan_id = form.get("plan_id", "").strip()
    if not re.fullmatch(ID_PATTERN, plan_id):
        errors.append("name: letters, digits, _ and - only, up to 128 characters")
    elif plan_id == "plan" or (settings.agreements_dir / plan_id).exists():
        errors.append(f"name: an agreement called {plan_id} already exists")
    problem = form.get("problem", "").strip().replace("\r\n", "\n")
    if not 1 <= len(problem) <= 16384:
        errors.append("question: required, up to 16384 characters")
    if errors:
        return errors, None

    target = settings.agreements_dir / plan_id
    if form.get("source") == "copy":
        _copy_folder(settings.plan_dir, target)
    else:
        _copy_folder(Path(__file__).with_name("template"), target)
    for role in PARTIES:
        sig = target / "signatures" / f"{role}.sig"
        if sig.is_file():
            sig.unlink()
    evaluation = target / "tests" / "evaluation.json"
    if evaluation.is_file():
        evaluation.unlink()
    path = target / "plan.json"
    config = decode(path.read_bytes())
    config["plan_id"] = plan_id
    config["classification"]["problem"] = problem
    config["classification"]["evaluation"] = {
        "test_set_digest": PLACEHOLDER_DIGEST,
        "results_digest": PLACEHOLDER_DIGEST,
    }
    if form.get("source") != "copy":  # the template takes the pinned model from the trust file
        model = load_trust(settings)["model"]
        config["model"] = {
            k: model[k] for k in ("id", "artifact_digest", "temperature", "max_output_tokens")
        }
    path.write_bytes(canonical(config) + b"\n")
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
    import shutil

    if agreement_id == "plan":
        return "the deploy slot cannot be deleted"
    target = for_agreement(settings, agreement_id)
    if target is None:
        return "unknown agreement"
    shutil.rmtree(target.plan_dir)  # the deployed copy, if any, stays in plan/ and in git
    return None


def check_public_key(public_key: str) -> str | None:
    """A base64 Ed25519 public key, or why it is not one. The trust file itself is never
    written by the UI: a key changes through a pull request against the deploy branch."""
    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key.strip(), validate=True))
    except Exception:  # noqa: BLE001 - any decoding or key error means "not a key"
        return "that is not a base64 Ed25519 public key (44 characters, ending in =)"
    return None
