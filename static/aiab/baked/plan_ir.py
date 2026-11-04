"""The audit plan as a graph (``aiab-plan-ir/v0``): schema, checks and derived settings.

A plan is one JSON document: a list of nodes, each with typed inputs that read another
node's output. Five node types: ``input_logs`` (the records of a run), ``classifier`` (one
model call per item, a label and a score each), ``run_python`` (a ``run`` function over its
inputs), ``run_llm`` (a model call per item, or one call in total) and ``final_report`` (the
only thing that leaves the box). What travels between nodes is one of four port types:
LogRecords, Items, Classifications or Value.

This module mirrors the builder's rules (ui/playground/src/plan: types, validate, lint,
report), so a plan the builder calls valid is a plan the box accepts, and the other way
round. The builder's warnings are not errors here either. Everything the box needs beyond
the plan itself (the output schema, the run limits, the input scope) is derived here, once,
so the rest of the runtime keeps reading ``bundle.config`` as before.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator

from aiab.audit.protocol import DATE, AuditError

PLAN_VERSION = "aiab-plan-ir/v0"
PORT_TYPES = ("LogRecords", "Items", "Classifications", "Value")
FIELD_TYPES = ("boolean", "integer", "number", "string")
KINDS = ("request", "response", "tool_call", "tool_result")
NODE_TYPES = ("input_logs", "classifier", "run_python", "run_llm", "final_report")
REPORT_INPUT = "result"
ABSTAIN = "abstain"

# The box's own ceilings. A plan sets its limits below these; the manifest publishes them.
LIMITS = {
    "max_input_utf8_bytes": 1_000_000,
    "max_records_per_run": 2000,
    "max_model_calls_per_run": 256,
    "max_runtime_seconds_per_run": 300,
    "max_result_bytes": 4096,
}
# Whole modules a ``run_python`` step may import. Pure computation only; the seccomp
# sandbox, not this list, is the isolation boundary.
PYTHON_MODULES = ("re", "math", "collections", "itertools", "statistics", "json")
FORBIDDEN_NAMES = {
    "eval",
    "exec",
    "compile",
    "open",
    "globals",
    "locals",
    "vars",
    "getattr",
    "setattr",
    "delattr",
    "breakpoint",
    "input",
    "__import__",
}
MAX_CODE_BYTES = 65536

IDENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
IDENT = {"type": "string", "pattern": IDENT_RE.pattern, "maxLength": 64}
PLAN_ID = {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,128}$"}
REF = {"type": "string", "pattern": r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$", "maxLength": 129}
SHORT = {"type": "string", "maxLength": 1024}
TEXT = {"type": "string", "maxLength": 16384}
CODE = {"type": "string", "maxLength": MAX_CODE_BYTES}
FLAG = {"type": "boolean"}


def _obj(properties: dict, optional: tuple[str, ...] = ()) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": [k for k in properties if k not in optional],
        "additionalProperties": False,
    }


def _list(items: dict, maximum: int, minimum: int = 0, unique: bool = False) -> dict:
    out = {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}
    if unique:
        out["uniqueItems"] = True
    return out


MODEL = _obj(
    {
        "id": {"type": "string", "minLength": 1, "maxLength": 128},
        "temperature": {"type": "number", "minimum": 0, "maximum": 2},
        "max_output_tokens": {"type": "integer", "minimum": 32, "maximum": 2048},
    }
)
OUTPUT = _obj(
    {
        "name": IDENT,
        "type": {"enum": list(PORT_TYPES)},
        "fields": {
            "type": "object",
            "propertyNames": IDENT,
            "additionalProperties": {"enum": list(FIELD_TYPES)},
            "maxProperties": 16,
        },
    }
)
CONFIGS = {
    "input_logs": _obj(
        {
            "source": {"type": "string", "minLength": 1, "maxLength": 256},
            "input_scope": _obj(
                {
                    "window_start": DATE,
                    "window_end": DATE,
                    "agents": _list(
                        {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,128}$"},
                        100,
                        unique=True,
                    ),
                },
                optional=("window_start", "window_end"),
            ),
            "filter": _obj(
                {
                    "kinds": _list({"enum": list(KINDS)}, 4, minimum=1, unique=True),
                    "keywords": _list(SHORT, 32),
                }
            ),
            "max_records": {
                "type": "integer",
                "minimum": 1,
                "maximum": LIMITS["max_records_per_run"],
            },
        }
    ),
    "classifier": _obj(
        {
            "model": MODEL,
            "group_by": {"enum": ["session", "record"]},
            "window": {"type": "integer", "minimum": 1, "maximum": 32},
            "instructions": TEXT,
            "labels": _list(
                _obj({"name": IDENT, "description": SHORT, "flag": FLAG}), 32, minimum=1
            ),
            "threshold": {"type": "number", "minimum": 0, "maximum": 1},
            "positive_examples": _list(TEXT, 32),
            "negative_examples": _list(TEXT, 32),
            "output_contract": _obj({"score": FLAG, "evidence_ids": FLAG}),
        },
        optional=("threshold",),
    ),
    "run_python": _obj({"code": CODE, "output": OUTPUT}),
    "run_llm": _obj({"model": MODEL, "system_prompt": TEXT, "user_prompt": TEXT, "output": OUTPUT}),
    "final_report": _obj(
        {
            "fields": _list(
                _obj(
                    {
                        "name": IDENT,
                        "type": {"enum": list(FIELD_TYPES)},
                        "from": {"type": "string", "minLength": 1, "maxLength": 256},
                        "description": SHORT,
                    },
                    optional=("from", "description"),
                ),
                16,
            ),
            "privacy": _obj(
                {
                    "allow_free_text": FLAG,
                    "min_count": {"type": "integer", "minimum": 1, "maximum": 1_000_000},
                    "max_result_bytes": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": LIMITS["max_result_bytes"],
                    },
                }
            ),
        }
    ),
}
NODE = _obj(
    {
        "id": IDENT,
        "type": {"enum": list(NODE_TYPES)},
        "title": SHORT,
        "inputs": {"type": "object", "propertyNames": IDENT, "additionalProperties": REF},
        "config": {"type": "object"},
    },
    optional=("title",),
)
PLAN = _obj(
    {
        "version": {"const": PLAN_VERSION},
        "plan_id": PLAN_ID,
        "title": SHORT,
        "description": TEXT,
        "limits": _obj(
            {
                k: {"type": "integer", "minimum": 1, "maximum": LIMITS[k]}
                for k in (
                    "max_input_utf8_bytes",
                    "max_model_calls_per_run",
                    "max_runtime_seconds_per_run",
                )
            }
        ),
        "nodes": _list({"type": "object"}, 64, minimum=1),
    },
    optional=("description",),
)

# What each node type accepts on its inputs, and how its inputs are named.
ACCEPTS = {
    "input_logs": (),
    "classifier": ("LogRecords", "Items"),
    "run_python": PORT_TYPES,
    "run_llm": PORT_TYPES,
    # Only aggregated results may be disclosed: never raw records or per-item text.
    "final_report": ("Classifications", "Value"),
}
INPUT_RULES = {
    "input_logs": ("none", ()),
    "classifier": ("fixed", ("items",)),
    "run_python": ("dynamic", ()),
    "run_llm": ("dynamic", ()),
    "final_report": ("fixed", (REPORT_INPUT,)),
}
LIST_TYPES = ("LogRecords", "Items")
PLACEHOLDER = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")


class PlanError(AuditError):
    """``invalid_plan`` with the reasons: each issue has a message, a node id and a path."""

    def __init__(self, issues: list[dict[str, Any]]):
        super().__init__("invalid_plan")
        self.issues = issues

    def __str__(self) -> str:
        return "invalid_plan: " + "; ".join(i["message"] for i in self.issues[:5])


@dataclass(frozen=True)
class Analysis:
    """A checked plan and what the runtime derives from it."""

    plan: dict
    nodes: dict[str, dict]
    order: tuple[str, ...]
    output_schema: dict
    limits: dict
    input_scope: dict | None
    report: dict
    source: dict = field(repr=False)  # the input_logs node

    @property
    def config(self) -> dict:
        """The shape the rest of the box reads: id, limits, output schema, input scope."""
        cfg = {
            "plan_id": self.plan["plan_id"],
            "limits": dict(self.limits),
            "output_schema": self.output_schema,
        }
        if self.input_scope:
            cfg["input_scope"] = self.input_scope
        return cfg


def output_port(node: dict) -> tuple[str, str] | None:
    """(name, type) of a node's single output; None for the Final Report."""
    t = node["type"]
    if t == "input_logs":
        return "records", "LogRecords"
    if t == "classifier":
        return "results", "Classifications"
    if t in ("run_python", "run_llm"):
        return node["config"]["output"]["name"], node["config"]["output"]["type"]
    return None


def split_ref(ref: str) -> tuple[str, str]:
    source, port = ref.split(".", 1)
    return source, port


def label_names(node: dict) -> list[str]:
    names = [lab["name"] for lab in node["config"]["labels"]]
    return names + [ABSTAIN] if node["config"].get("threshold") is not None else names


def assignable(declared: str, actual: str) -> bool:
    return declared == actual or (declared == "number" and actual == "integer")


def resolve_report_path(nodes: dict[str, dict], report: dict, path: str) -> tuple[str, str]:
    """(field type, "") when the path resolves in the report's input, else ("", why not)."""
    ref = report["inputs"].get(REPORT_INPUT)
    if not ref:
        return "", "the report has no input connected"
    src = nodes.get(split_ref(ref)[0])
    if src is None:
        return "", "the report's input does not resolve"
    if src["type"] == "classifier":
        out = {"type": "Classifications", "fields": {}}
    elif src["type"] in ("run_python", "run_llm"):
        out = src["config"]["output"]
    else:
        return "", f'"{src["id"]}" cannot be reported'
    parts = path.split(".")
    if out["type"] == "Value":
        if len(parts) == 1 and parts[0] in out["fields"]:
            return out["fields"][parts[0]], ""
        fields = ", ".join(out["fields"]) or "none"
        return "", f'"{src["id"]}" declares no field "{path}" (fields: {fields})'
    if out["type"] == "Classifications":
        labels = label_names(src) if src["type"] == "classifier" else None
        head, label, *extra = parts + [None] if len(parts) == 1 else parts
        if extra:
            return "", "path too long"
        if head == "count" and label is None:
            return "integer", ""
        if head == "max_score" and label is None:
            return "number", ""
        if head in ("count", "proportion", "any"):
            if not label:
                return "", "missing label"
            if labels is not None and label not in labels:
                return "", f'unknown label "{label}" (labels: {", ".join(labels)})'
            return {"count": "integer", "proportion": "number", "any": "boolean"}[head], ""
        return "", (
            "classifications are reported only as aggregates: count, max_score, "
            "count.<label>, proportion.<label>, any.<label>"
        )
    return "", f"{out['type']} cannot be reported"


def check_code(code: str, inputs: tuple[str, ...]) -> str | None:
    """Why a ``run_python`` step's code is not admitted, or None.

    The module may hold a docstring, imports of a few pure modules, constants and
    functions, and must define ``run`` taking exactly the step's inputs by name (or
    ``**inputs``). Dunder and private attribute access and the reflective builtins are
    refused. This is a restriction on what the parties can agree to, not the sandbox.
    """
    if len(code.encode("utf-8")) > MAX_CODE_BYTES:
        return "code is longer than 64 KiB"
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError) as exc:
        return f"syntax error: {getattr(exc, 'msg', exc)}"
    run = None
    for node in tree.body:
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        if isinstance(node, ast.FunctionDef):
            if node.name == "run":
                run = node
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign)):
            continue
        return f"line {node.lineno}: only imports, constants and functions at module level"
    if run is None:
        return "define run(...) taking the step's inputs"
    args = run.args
    if args.vararg or args.kwonlyargs or args.posonlyargs or args.defaults:
        return "run() takes its inputs as plain named parameters, or **inputs"
    names = [a.arg for a in args.args]
    if not args.kwarg and sorted(names) != sorted(inputs):
        want = ", ".join(inputs) or "nothing"
        return f"run({', '.join(names)}) does not match the inputs ({want})"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in PYTHON_MODULES:
                    return f"line {node.lineno}: import {alias.name} is not allowed"
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in PYTHON_MODULES:
                return f"line {node.lineno}: import from {node.module} is not allowed"
            if any(a.name.startswith("_") for a in node.names):
                return f"line {node.lineno}: private names cannot be imported"
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            return f"line {node.lineno}: private attribute {node.attr}"
        elif isinstance(node, ast.Name) and (
            node.id.startswith("__") or node.id in FORBIDDEN_NAMES
        ):
            return f"line {node.lineno}: {node.id} is not allowed"
        elif isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.Global, ast.Nonlocal)):
            return f"line {node.lineno}: classes, async and global statements are not allowed"
        elif isinstance(node, ast.FunctionDef) and node.decorator_list:
            return f"line {node.lineno}: decorators are not allowed"
    return None


def _schema_issues(schema: dict, value: Any, prefix: list) -> list[dict]:
    issues = []
    for error in sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: e.path):
        path = prefix + list(error.absolute_path)
        issues.append({"message": f"{_dotted(path)}: {error.message}", "path": path})
    return issues


def _dotted(path: list) -> str:
    out = ""
    for p in path:
        out += f"[{p}]" if isinstance(p, int) else (("." if out else "") + str(p))
    return out or "plan"


def check_plan(raw: Any, trust_model: dict | None = None) -> Analysis:
    """Check a plan document and derive the runtime's settings. Raises PlanError."""
    issues: list[dict] = []

    def issue(message: str, node: str | None = None, path: list | None = None) -> None:
        issues.append({"message": message, "node": node, "path": path or []})

    if not isinstance(raw, dict):
        raise PlanError([{"message": "the plan must be a JSON object", "node": None, "path": []}])
    issues += [{**i, "node": None} for i in _schema_issues(PLAN, raw, [])]
    if issues:
        raise PlanError(issues)
    nodes_raw = raw["nodes"]
    for i, n in enumerate(nodes_raw):
        node_id = n.get("id") if isinstance(n.get("id"), str) else None
        found = _schema_issues(NODE, n, ["nodes", i])
        if not found and n["type"] in CONFIGS:
            found = _schema_issues(CONFIGS[n["type"]], n["config"], ["nodes", i, "config"])
        issues += [{**f, "node": node_id} for f in found]
    if issues:
        raise PlanError(issues)

    # Structure: unique ids, inputs that resolve, no cycles.
    nodes: dict[str, dict] = {}
    for i, n in enumerate(nodes_raw):
        if n["id"] in nodes:
            issue(f'Duplicate node id "{n["id"]}"', n["id"], ["nodes", i, "id"])
        else:
            nodes[n["id"]] = n
    for i, n in enumerate(nodes_raw):
        kind, ports = INPUT_RULES[n["type"]]
        for name, ref in n["inputs"].items():
            path = ["nodes", i, "inputs", name]
            if kind == "none":
                issue(f"{n['id']}: Input Logs takes no inputs", n["id"], path)
                continue
            if kind == "fixed" and name not in ports:
                issue(
                    f'{n["id"]}: unknown input "{name}" (allowed: {", ".join(ports)})',
                    n["id"],
                    path,
                )
                continue
            src_id, port = split_ref(ref)
            src = nodes.get(src_id)
            if src is None:
                issue(f'{n["id"]}.inputs.{name}: no node "{src_id}"', n["id"], path)
                continue
            if src_id == n["id"]:
                issue(f"{n['id']}: a node cannot read its own output", n["id"], path)
                continue
            out = output_port(src)
            if out is None:
                issue(
                    f'{n["id"]}.inputs.{name}: "{src_id}" is a Final Report and has no output',
                    n["id"],
                    path,
                )
            elif out[0] != port:
                issue(
                    f'{n["id"]}.inputs.{name}: "{src_id}" has no output "{port}"'
                    f' (its output is "{out[0]}")',
                    n["id"],
                    path,
                )
    if issues:
        raise PlanError(issues)
    order = _topo_order(nodes)
    if order is None:
        issue("Cycle: a plan must be a directed acyclic graph")
        raise PlanError(issues)

    # Semantics: the builder's lint errors, plus what the box pins.
    sources = [n for n in nodes_raw if n["type"] == "input_logs"]
    reports = [n for n in nodes_raw if n["type"] == "final_report"]
    if len(sources) != 1:
        issue("A plan has exactly one Input Logs node")
    if len(reports) != 1:
        issue("A plan has exactly one Final Report")

    def input_type(n: dict, name: str) -> str | None:
        ref = n["inputs"].get(name)
        if not ref:
            return None
        out = output_port(nodes[split_ref(ref)[0]])
        return out[1] if out else None

    for i, n in enumerate(nodes_raw):
        nid, cfg = n["id"], n["config"]
        at = ["nodes", i]
        for name in n["inputs"]:
            t = input_type(n, name)
            if t and t not in ACCEPTS[n["type"]]:
                hint = (
                    ". Aggregate raw content first (Classifier or Run Python)"
                    if n["type"] == "final_report"
                    else ""
                )
                issue(
                    f"{nid}.{name}: receives {t}, but this node accepts "
                    f"{' or '.join(ACCEPTS[n['type']])}{hint}",
                    nid,
                    at + ["inputs", name],
                )
        if n["type"] in ("classifier", "run_llm") and trust_model is not None:
            model = cfg["model"]
            if model["id"] != trust_model["id"]:
                issue(
                    f'{nid}: model "{model["id"]}" is not the pinned model ({trust_model["id"]})',
                    nid,
                    at + ["config", "model", "id"],
                )
            if model["temperature"] != 0:
                issue(
                    f"{nid}: the box runs the model at temperature 0",
                    nid,
                    at + ["config", "model", "temperature"],
                )
        if n["type"] == "classifier":
            if not n["inputs"].get("items"):
                issue(f"{nid}: connect logs or items to classify", nid, at + ["inputs"])
            names = [lab["name"] for lab in cfg["labels"]]
            if len(names) < 2:
                issue(
                    f"{nid}: a classifier needs at least two labels", nid, at + ["config", "labels"]
                )
            dup = next((x for k, x in enumerate(names) if names.index(x) != k), None)
            if dup:
                issue(f'{nid}: label "{dup}" appears twice', nid, at + ["config", "labels"])
            if ABSTAIN in names:
                issue(
                    f'{nid}: "{ABSTAIN}" is reserved for scores below the threshold',
                    nid,
                    at + ["config", "labels"],
                )
        if n["type"] in ("run_python", "run_llm"):
            ins = list(n["inputs"])
            if not ins:
                issue(f"{nid}: connect at least one input", nid, at + ["inputs"])
            out = cfg["output"]
            if out["type"] != "Value" and out["fields"]:
                issue(f"{nid}: only a Value output declares fields", nid, at + ["config", "output"])
            if n["type"] == "run_python":
                why = check_code(cfg["code"], tuple(ins))
                if why:
                    issue(f"{nid}: {why}", nid, at + ["config", "code"])
            else:
                for m in PLACEHOLDER.finditer(cfg["user_prompt"]):
                    if m.group(1) not in ins:
                        issue(
                            f"{nid}: prompt uses {{{{{m.group(1)}}}}}, which is not an input",
                            nid,
                            at + ["config", "user_prompt"],
                        )
                if out["type"] not in ("Items", "Value"):
                    issue(
                        f"{nid}: a model call produces Items (one answer per item) or a Value",
                        nid,
                        at + ["config", "output", "type"],
                    )
                lists = [k for k in ins if input_type(n, k) in LIST_TYPES]
                if out["type"] == "Items" and len(lists) != 1:
                    issue(
                        f"{nid}: an Items output runs once per item, so it needs exactly one"
                        f" LogRecords or Items input (has {len(lists)})",
                        nid,
                        at + ["config", "output", "type"],
                    )
                if out["type"] == "Value" and not out["fields"]:
                    issue(
                        f"{nid}: a model call producing a Value must declare its fields",
                        nid,
                        at + ["config", "output", "fields"],
                    )
        if n["type"] == "final_report":
            if not n["inputs"].get(REPORT_INPUT):
                issue(f"{nid}: connect the one result to release", nid, at + ["inputs"])
            if not cfg["fields"]:
                issue(f"{nid}: the report has no fields", nid, at + ["config", "fields"])
            seen: set[str] = set()
            for k, f in enumerate(cfg["fields"]):
                p = at + ["config", "fields", k]
                if f["name"] in seen:
                    issue(f'{nid}: field "{f["name"]}" appears twice', nid, p + ["name"])
                seen.add(f["name"])
                path = f.get("from", f["name"])
                if n["inputs"].get(REPORT_INPUT):
                    actual, why = resolve_report_path(nodes, n, path)
                    if why:
                        issue(
                            f"{nid}.{f['name']}: {why}",
                            nid,
                            p + ["from" if "from" in f else "name"],
                        )
                    elif not assignable(f["type"], actual):
                        issue(
                            f"{nid}.{f['name']}: declared {f['type']} but {path} is {actual}",
                            nid,
                            p + ["type"],
                        )
                if f["type"] == "string" and not cfg["privacy"]["allow_free_text"]:
                    issue(
                        f"{nid}.{f['name']}: string fields can carry free text; set"
                        " privacy.allow_free_text to release them",
                        nid,
                        p + ["type"],
                    )
    if issues:
        raise PlanError(issues)

    source, report = sources[0], reports[0]
    scope = source["config"]["input_scope"]
    input_scope = None
    if scope.get("window_start") and scope.get("window_end") and scope["agents"]:
        input_scope = {
            "logger_key_id": "logger",
            "window_start": scope["window_start"],
            "window_end": scope["window_end"],
            "agents": sorted(scope["agents"]),
        }
    limits = {
        **raw["limits"],
        "max_records_per_run": source["config"]["max_records"],
        "max_result_bytes": report["config"]["privacy"]["max_result_bytes"],
    }
    return Analysis(
        plan=raw,
        nodes=nodes,
        order=tuple(order),
        output_schema=report_schema(report),
        limits=limits,
        input_scope=input_scope,
        report=report,
        source=source,
    )


def report_schema(report: dict) -> dict:
    """The report's JSON Schema in the bounded form the egress gate checks (output.py)."""
    types = {
        "boolean": {"type": "boolean"},
        "integer": {"type": "integer", "minimum": -(2**31), "maximum": 2**31},
        "number": {"type": "number"},
        "string": {"type": "string", "maxLength": 1024},
    }
    fields = report["config"]["fields"]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {f["name"]: dict(types[f["type"]]) for f in fields},
        "required": [f["name"] for f in fields],
        "additionalProperties": False,
    }


def upstream(nodes: dict[str, dict]) -> dict[str, list[str]]:
    return {
        nid: list(dict.fromkeys(split_ref(r)[0] for r in n["inputs"].values()))
        for nid, n in nodes.items()
    }


def _topo_order(nodes: dict[str, dict]) -> list[str] | None:
    """Node ids with every node after its inputs, in plan order otherwise; None on a cycle."""
    ups = upstream(nodes)
    state: dict[str, int] = {}
    out: list[str] = []

    def visit(nid: str) -> bool:
        if state.get(nid) == 2:
            return True
        if state.get(nid) == 1:
            return False
        state[nid] = 1
        for u in ups.get(nid, []):
            if u in nodes and not visit(u):
                return False
        state[nid] = 2
        out.append(nid)
        return True

    for nid in nodes:
        if not visit(nid):
            return None
    return out
