"""View models over the plan folder: the pipeline description plus the files in it."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from aiab.plan_api import PIPELINE, Step
from aiab.plan_bundle import (
    PARTIES,
    Bundle,
    FunctionInfo,
    Issue,
    check_bundle,
    load_bundle,
    load_restrictions,
    load_trusted_parties,
    plan_functions,
    replace_function_source,
    signature_status,
    tests_digest,
    verify_signature,
)

from server.config import Settings


def get_setting(config: dict[str, Any], dotted: str) -> Any:
    node: Any = config
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
        return self.kind in {"list", "dict"}


@dataclass(frozen=True)
class StepView:
    step: Step
    function: FunctionInfo | None
    settings: tuple[SettingView, ...]
    issues: tuple[Issue, ...]
    prompt_id: str | None
    prompt_text: str | None

    @property
    def errors(self) -> int:
        return sum(1 for i in self.issues if i.level == "error")


@dataclass(frozen=True)
class PlanView:
    bundle: Bundle
    steps: tuple[StepView, ...]
    issues: tuple[Issue, ...]
    signatures: dict[str, dict[str, Any]]
    frozen: bool
    state: str
    state_badge: str
    trusted_pinned: bool
    restrictions: dict[str, Any]
    evaluation: dict[str, Any] | None
    evaluation_current: bool
    prompts: dict[str, str]

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def signed_by_both(self) -> bool:
        return all(s["status"] == "ok" for s in self.signatures.values())

    def step(self, name: str) -> StepView | None:
        return next((s for s in self.steps if s.step.name == name), None)


def _issues_for(step: Step, issues: list[Issue]) -> tuple[Issue, ...]:
    picked = []
    for issue in issues:
        where = issue.where
        if where == "plan.py" and step.name in issue.message:
            picked.append(issue)
        elif any(
            where == f"plan.json:{p}" or where.startswith(f"plan.json:{p}.") for p in step.settings
        ):
            picked.append(issue)
        elif step.name == "classify" and where.startswith("plan.json:classification.prompt_id"):
            picked.append(issue)
    return tuple(picked)


def agreement_state(
    signatures: dict[str, dict[str, Any]],
    frozen: bool,
    issues: list[Issue],
    evaluation_current: bool,
) -> tuple[str, str]:
    ok = [p for p, s in signatures.items() if s["status"] == "ok"]
    if len(ok) == len(PARTIES):
        return "signed by both", "badge--ok"
    bad = {"stale", "invalid", "malformed", "unknown_key"}
    if any(s["status"] in bad for s in signatures.values()):
        return "signature problem", "badge--danger"
    if ok:
        return f"signed by {ok[0]}", "badge--ok"
    if frozen:
        return "frozen", "badge--ink"
    if any(i.level == "error" for i in issues):
        return "drafting, has errors", "badge--danger"
    if not evaluation_current:
        return "drafting, dry run needed", "badge"
    return "drafting, ready to freeze", "badge"


def load_plan_view(settings: Settings) -> PlanView:
    bundle = load_bundle(settings.plan_dir)
    restrictions = load_restrictions()
    trusted = load_trusted_parties(settings.trusted_parties)
    pinned = trusted.get("parties") or {}
    trusted_pinned = all((pinned.get(p) or {}).get("public_key") for p in PARTIES)
    issues = check_bundle(bundle, restrictions, trusted if trusted_pinned else None)
    try:
        functions = plan_functions(bundle.path)
    except (OSError, SyntaxError):
        functions = {}
    prompts = {
        p.stem: p.read_text(encoding="utf-8")
        for p in sorted((bundle.path / "prompts").glob("*.txt"))
    }
    signatures = signature_status(bundle, trusted)
    frozen = any(s["present"] for s in signatures.values())

    evaluation = None
    evaluation_path = bundle.path / "tests" / "evaluation.json"
    if evaluation_path.is_file():
        try:
            evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
        except ValueError:
            evaluation = None
    recorded = get_setting(bundle.config, "classification.evaluation") or {}
    evaluation_current = bool(
        evaluation
        and evaluation.get("test_set_digest") == tests_digest(bundle.path)
        and recorded.get("results_digest") == evaluation.get("results_digest")
    )

    prompt_id = get_setting(bundle.config, "classification.prompt_id")
    steps = []
    for step in PIPELINE:
        values = tuple(
            SettingView(
                path=path,
                value=get_setting(bundle.config, path),
                value_json=json.dumps(get_setting(bundle.config, path), indent=2),
                kind=_kind(get_setting(bundle.config, path)),
            )
            for path in step.settings
        )
        steps.append(
            StepView(
                step=step,
                function=functions.get(step.name),
                settings=values,
                issues=_issues_for(step, issues),
                prompt_id=prompt_id if step.kind == "run_llm" else None,
                prompt_text=prompts.get(prompt_id) if step.kind == "run_llm" else None,
            )
        )
    state, badge = agreement_state(signatures, frozen, issues, evaluation_current)
    return PlanView(
        bundle=bundle,
        steps=tuple(steps),
        issues=tuple(issues),
        signatures=signatures,
        frozen=frozen,
        state=state,
        state_badge=badge,
        trusted_pinned=trusted_pinned,
        restrictions=restrictions,
        evaluation=evaluation,
        evaluation_current=evaluation_current,
        prompts=prompts,
    )


# --------------------------------------------------------------------------- edits


def _write_config(settings: Settings, config: dict[str, Any]) -> None:
    (settings.plan_dir / "plan.json").write_text(
        json.dumps(config, indent=2) + "\n", encoding="utf-8"
    )


def save_step_settings(settings: Settings, step_name: str, form: dict[str, str]) -> list[str]:
    step = next((s for s in PIPELINE if s.name == step_name), None)
    if step is None:
        return [f"unknown step {step_name}"]
    bundle = load_bundle(settings.plan_dir)
    config = dict(bundle.config)
    errors: list[str] = []
    numeric = {"int", "float"}
    for path in step.settings:
        if path not in form:
            continue
        try:
            value = json.loads(form[path])
        except ValueError as exc:
            errors.append(f"{path}: not valid JSON ({exc})")
            continue
        current = get_setting(config, path)
        if current is not None and _kind(current) != _kind(value):
            if not (_kind(current) in numeric and _kind(value) in numeric):
                errors.append(f"{path}: expected {_kind(current)}, got {_kind(value)}")
                continue
        set_setting(config, path, value)
    if errors:
        return errors
    _write_config(settings, config)
    return []


def save_function(settings: Settings, name: str, source: str) -> str | None:
    try:
        replace_function_source(settings.plan_dir, name, source.replace("\r\n", "\n"))
    except (SyntaxError, ValueError, KeyError) as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def save_prompt(settings: Settings, prompt_id: str, text: str) -> str | None:
    path = settings.plan_dir / "prompts" / f"{prompt_id}.txt"
    if not path.is_file():
        return f"prompts/{prompt_id}.txt does not exist"
    text = text.replace("\r\n", "\n")
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")
    return None


def write_signature(settings: Settings, party: str, content: bytes) -> tuple[str, str]:
    """Verify an uploaded signature file against the pinned key, then store it."""
    bundle = load_bundle(settings.plan_dir)
    trusted = load_trusted_parties(settings.trusted_parties)
    try:
        sig = json.loads(content.decode("utf-8"))
    except ValueError:
        return "malformed", "file is not JSON"
    status, detail = verify_signature(sig, party, trusted, bundle.digest)
    if status != "ok":
        return status, detail
    target = settings.plan_dir / "signatures" / f"{party}.sig"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(sig, indent=2) + "\n", encoding="utf-8")
    return status, detail


def delete_signatures(settings: Settings) -> int:
    removed = 0
    for party in PARTIES:
        path = settings.plan_dir / "signatures" / f"{party}.sig"
        if path.is_file():
            path.unlink()
            removed += 1
    return removed


def run_dry_subprocess(settings: Settings, record: bool) -> dict[str, Any]:
    """Run the dry run out of process: plan.py is party-authored code."""
    command = [sys.executable, "-m", "aiab.dryrun", str(settings.plan_dir), "--json"]
    if record:
        command.append("--record")
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=settings.dry_run_timeout,
            cwd=str(settings.plan_dir.parent),
            env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except subprocess.TimeoutExpired:
        return {"error": f"dry run exceeded {settings.dry_run_timeout}s"}
    if completed.returncode not in (0, 1) or not completed.stdout.strip():
        return {
            "error": f"dry run exited {completed.returncode}",
            "stderr": completed.stderr[-2000:],
        }
    try:
        return json.loads(completed.stdout)
    except ValueError:
        return {"error": "dry run printed no JSON", "stderr": completed.stderr[-2000:]}
