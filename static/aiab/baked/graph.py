"""Runs a checked plan over a run's records: the engine the worker executes in the sandbox.

Nodes run in dependency order. Every value that travels between nodes is plain JSON data of
its port type: LogRecords are the run's records (each with an ``id``), Items are
``{id, text, record_ids}``, Classifications are ``{item_id, label, score, evidence_ids}``
and a Value is one object of declared scalar fields. The two model node types never call
a model here: they hand the texts to the ``llm`` callback, one batch per node, and the
parent broker makes the calls (executor.py). Deterministic for the same records and the
same model answers; it reads no clock, file or environment.

An item longer than the model's context is split, never cut: the parent says how many bytes
each model node's user message may have (``budgets``), the item's text is cut into
consecutive pieces that fit (at line ends where it can) and every piece is asked on its own.
The pieces join back into exactly the item's text. The answers merge into one per item:
for a classifier, the highest-scoring piece with a flagged label if any piece has one, else
the highest-scoring piece; for a free-form step, the pieces' texts joined by line breaks.
A Value step makes one call over all its inputs and cannot be split.

Plan code (``run_python``) runs here too, so this module is loaded into the worker before
the syscall filter is installed and nothing in it touches the operating system.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from functools import partial
from typing import Any

from aiab.baked.plan_ir import ABSTAIN, PLACEHOLDER, REPORT_INPUT, label_names, split_ref

RECORD_KEYS = ("stream", "batch", "offset", "agent_id", "session_id", "ts", "kind", "content")
Llm = Callable[[str, list[str]], list[dict]]


class StepError(ValueError):
    """A node failed; ``node_type`` picks the fixed error code the box reports."""

    def __init__(self, node: dict, why: str):
        super().__init__(f"{node['id']}: {why}")
        self.node_type = node["type"]


def record_id(record: dict) -> str:
    return f"{record['stream']}:{record['batch']}:{record['offset']}"


def with_ids(records: list[dict]) -> list[dict]:
    return [{"id": record_id(r), **{k: r[k] for k in RECORD_KEYS}} for r in records]


def select_records(node: dict, records: list[dict]) -> list[dict]:
    """The Input Logs step: kinds, keywords (any, case-insensitive), agents and, when the
    plan names them, sessions, in order."""
    cfg = node["config"]
    kinds = set(cfg["filter"]["kinds"])
    keywords = [k.lower() for k in cfg["filter"]["keywords"] if k]
    agents = set(cfg["input_scope"]["agents"])
    sessions = set(cfg["filter"].get("sessions") or ())
    kept = []
    for r in records:
        if r["kind"] not in kinds or (agents and r["agent_id"] not in agents):
            continue
        if sessions and r["session_id"] not in sessions:
            continue
        if keywords and not any(k in r["content"].lower() for k in keywords):
            continue
        kept.append(r)
    return kept


def item_text(records: list[dict]) -> str:
    return "\n".join(f"{r['kind']}: {r['content']}" for r in records)


def to_items(port_type: str, value: list, group_by: str, window: int | None) -> list[dict]:
    """Items from a list input: Items as they are; LogRecords grouped by session (in windows
    of ``window`` records, in log order; None for whole sessions) or one item per record."""
    if port_type == "Items":
        return value
    if group_by == "record":
        return [{"id": r["id"], "text": r["content"], "record_ids": [r["id"]]} for r in value]
    sessions: dict[str, list[dict]] = {}
    for r in value:
        sessions.setdefault(r["session_id"], []).append(r)
    items = []
    for sid, rs in sessions.items():
        rs = sorted(rs, key=lambda r: (r["batch"], r["offset"]))
        size = window or len(rs)
        for k in range(0, len(rs), size):
            chunk = rs[k : k + size]
            items.append(
                {
                    "id": sid if len(rs) <= size else f"{sid}#{k // size + 1}",
                    "text": item_text(chunk),
                    "record_ids": [r["id"] for r in chunk],
                }
            )
    return items


def split_to_fit(text: str, size: Callable[[str], int], budget: int | None) -> list[str]:
    """``text`` as consecutive pieces whose user message (``size``, in UTF-8 bytes) is at most
    ``budget``; ``"".join`` of the pieces is ``text``. A piece ends after a line break when one
    falls in its second half. One piece when it fits, when there is no budget, or when even
    an empty piece does not fit (the parent then refuses the prompt as too large)."""
    if budget is None or size(text) <= budget or size("") >= budget:
        return [text]
    pieces, start = [], 0
    while start < len(text):
        # The longest text[start:end] that fits ends in (lo, hi]; a character is a byte or more.
        lo, hi = start, min(len(text), start + budget)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if size(text[start:mid]) <= budget:
                lo = mid
            else:
                hi = mid - 1
        end = lo
        if end == start:
            return [text]  # not even one character fits beside the prompt
        if end < len(text):
            cut = text.rfind("\n", start, end)
            if cut >= start + (end - start) // 2:
                end = cut + 1
        pieces.append(text[start:end])
        start = end
    return pieces


def classifier_size(text: str) -> int:
    """The size of the classifier's user message for ``text``: the canonical JSON that
    prompts.classifier_request builds. For one string field, RFC 8785 and ``json.dumps``
    without ASCII escaping write the same bytes; the canonical encoder itself loads a codec
    at first use, which the sandbox no longer allows."""
    message = json.dumps({"audit_text": text}, ensure_ascii=False, separators=(",", ":"))
    return len(message.encode("utf-8"))


def item_prompt(template: str, others: dict[str, str], name: str, text: str) -> str:
    """A free-form step's user message for one item (or piece of one)."""
    return render(template, {**others, name: text})


def prompt_size(prompt: Callable[[str], str], text: str) -> int:
    return len(prompt(text).encode("utf-8"))


def ask_in_pieces(llm: Llm, nid: str, texts: list[list[str]], node: dict) -> list[list[dict]]:
    """Ask every piece of every item in one batch; the answers grouped per item."""
    flat = [piece for pieces in texts for piece in pieces]
    answers = llm(nid, flat)
    if len(answers) != len(flat):
        raise StepError(node, "the model answered a different number of items")
    grouped, k = [], 0
    for pieces in texts:
        grouped.append(answers[k : k + len(pieces)])
        k += len(pieces)
    return grouped


def merge_labels(node: dict, answers: list[dict]) -> dict:
    """One answer for an item asked in pieces: the best flagged piece, else the best piece."""
    flagged = {lab["name"] for lab in node["config"]["labels"] if lab["flag"]}
    pool = [a for a in answers if a["label"] in flagged] or answers
    return max(pool, key=lambda a: a["score"])


def check_scalar(kind: str, value: Any) -> bool:
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return (
            isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
        )
    return isinstance(value, str)


def check_value(node: dict, fields: dict[str, str], value: Any) -> dict:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise StepError(node, "output does not have exactly the declared fields")
    for name, kind in fields.items():
        if not check_scalar(kind, value[name]):
            raise StepError(node, f"field {name} is not a {kind}")
    return {k: value[k] for k in fields}


def check_items(node: dict, value: Any) -> list[dict]:
    if not isinstance(value, list):
        raise StepError(node, "Items output must be a list")
    items, seen = [], set()
    for it in value:
        if (
            not isinstance(it, dict)
            or not isinstance(it.get("id"), str)
            or not isinstance(it.get("text"), str)
            or not isinstance(it.get("record_ids"), list)
            or any(not isinstance(x, str) for x in it["record_ids"])
            or it["id"] in seen
        ):
            raise StepError(node, "each item needs a unique id, a text and record_ids")
        seen.add(it["id"])
        items.append({"id": it["id"], "text": it["text"], "record_ids": list(it["record_ids"])})
    return items


def check_classifications(node: dict, value: Any) -> list[dict]:
    if not isinstance(value, list):
        raise StepError(node, "Classifications output must be a list")
    out = []
    for c in value:
        score = c.get("score", 0.0) if isinstance(c, dict) else None
        if (
            not isinstance(c, dict)
            or not isinstance(c.get("item_id"), str)
            or not isinstance(c.get("label"), str)
            or not check_scalar("number", score)
            or not 0 <= score <= 1
            or any(not isinstance(x, str) for x in c.get("evidence_ids", []))
        ):
            raise StepError(node, "each classification needs item_id, label, score, evidence_ids")
        out.append(
            {
                "item_id": c["item_id"],
                "label": c["label"],
                "score": float(score),
                "evidence_ids": list(c.get("evidence_ids", [])),
            }
        )
    return out


def check_records(node: dict, value: Any, known: dict[str, dict]) -> list[dict]:
    if not isinstance(value, list):
        raise StepError(node, "LogRecords output must be a list")
    out, seen = [], set()
    for r in value:
        if not isinstance(r, dict) or r.get("id") not in known or r["id"] in seen:
            raise StepError(node, "LogRecords output may only carry the run's own records")
        seen.add(r["id"])
        out.append(dict(known[r["id"]]))
    return out


def exposed(port_type: str, value: Any, source: dict) -> Any:
    """A copy of a value as a consumer sees it; classifier results follow the contract."""
    copy = json.loads(json.dumps(value))
    if port_type == "Classifications" and source["type"] == "classifier":
        contract = source["config"]["output_contract"]
        for c in copy:
            if not contract["score"]:
                del c["score"]
            if not contract["evidence_ids"]:
                del c["evidence_ids"]
    return copy


def render(template: str, values: dict[str, Any]) -> str:
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def as_text(port_type: str, value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def aggregate(classifications: list[dict], labels: list[str] | None, path: str) -> Any:
    head, _, label = path.partition(".")
    n = len(classifications)
    with_label = sum(1 for c in classifications if c["label"] == label)
    if head == "count":
        return n if not label else with_label
    if head == "max_score":
        return round(max((c["score"] for c in classifications), default=0.0), 3)
    if head == "proportion":
        return round(with_label / n, 3) if n else 0.0
    if head == "any":
        return with_label > 0 and (labels is None or label in labels)
    raise ValueError(path)


def build_report(report: dict, source: dict, port_type: str, value: Any) -> dict:
    cfg = report["config"]
    labels = label_names(source) if source["type"] == "classifier" else None
    result = {}
    for f in cfg["fields"]:
        path = f.get("from", f["name"])
        x = value[path] if port_type == "Value" else aggregate(value, labels, path)
        if f["type"] == "integer" and 0 < x < cfg["privacy"]["min_count"]:
            x = 0  # suppressed: a small count would single out a session
        if f["type"] == "number" and isinstance(x, int):
            x = float(x)
        if not check_scalar(f["type"], x):
            raise StepError(report, f"field {f['name']} is not a {f['type']}")
        result[f["name"]] = x
    return result


def run_plan(
    plan: dict,
    order: list[str],
    records: list[dict],
    llm: Llm,
    compiled: dict[str, Any] | None = None,
    progress: Callable[[dict], None] | None = None,
    budgets: dict[str, int] | None = None,
) -> dict:
    """Execute the plan and return the report's result. ``compiled`` holds the code objects
    of the run_python steps when the caller compiled them already (the worker does, before
    the syscall filter goes up); ``progress`` is told which node starts, so a failure can
    be reported by stage without any detail; ``budgets`` gives each model node's largest
    user message in bytes (none: nothing is split)."""
    budgets = budgets or {}
    nodes = {n["id"]: n for n in plan["nodes"]}
    outputs: dict[str, tuple[str, Any]] = {}
    known: dict[str, dict] = {}
    result: dict | None = None

    def inputs_of(node: dict) -> list[tuple[str, str, Any, dict]]:
        out = []
        for name, ref in node["inputs"].items():
            src = split_ref(ref)[0]
            port_type, value = outputs[src]
            out.append((name, port_type, exposed(port_type, value, nodes[src]), nodes[src]))
        return out

    for nid in order:
        node = nodes[nid]
        cfg = node["config"]
        kind = node["type"]
        if progress is not None:
            progress(node)
        if kind == "input_logs":
            kept = select_records(node, with_ids(records))
            known = {r["id"]: r for r in kept}
            outputs[nid] = ("LogRecords", kept)
        elif kind == "classifier":
            _, port_type, value, _src = inputs_of(node)[0]
            items = to_items(port_type, value, cfg["group_by"], cfg["window"])
            budget = budgets.get(nid)
            pieces = [split_to_fit(it["text"], classifier_size, budget) for it in items]
            answers = [merge_labels(node, a) for a in ask_in_pieces(llm, nid, pieces, node)]
            threshold = cfg.get("threshold")
            results = []
            for it, a in zip(items, answers, strict=True):
                label = a["label"]
                if threshold is not None and a["score"] < threshold:
                    label = ABSTAIN
                results.append(
                    {
                        "item_id": it["id"],
                        "label": label,
                        "score": float(a["score"]),
                        "evidence_ids": list(it["record_ids"]),
                    }
                )
            outputs[nid] = ("Classifications", results)
        elif kind == "run_python":
            try:
                code = (compiled or {}).get(nid) or compile(cfg["code"], f"plan/{nid}.py", "exec")
                namespace: dict[str, Any] = {"__name__": f"plan.{nid}"}
                exec(code, namespace)  # noqa: S102 - the signed plan's own code, sandboxed
                value = namespace["run"](**{name: v for name, _, v, _ in inputs_of(node)})
            except StepError:
                raise
            except Exception as exc:
                raise StepError(node, type(exc).__name__) from None
            outputs[nid] = (cfg["output"]["type"], _check_output(node, value, known))
        elif kind == "run_llm":
            ins = inputs_of(node)
            out = cfg["output"]
            if out["type"] == "Items":
                name, port_type, value, _ = next(i for i in ins if i[1] in ("LogRecords", "Items"))
                items = to_items(port_type, value, "session", None)
                others = {n: as_text(t, v) for n, t, v, _ in ins if n != name}
                prompt = partial(item_prompt, cfg["user_prompt"], others, name)
                size = partial(prompt_size, prompt)
                pieces = [split_to_fit(it["text"], size, budgets.get(nid)) for it in items]
                texts = [[prompt(p) for p in ps] for ps in pieces]
                answers = [
                    {"text": "\n".join(a["text"] for a in parts)}
                    for parts in ask_in_pieces(llm, nid, texts, node)
                ]
                outputs[nid] = (
                    "Items",
                    [
                        {"id": it["id"], "text": a["text"], "record_ids": list(it["record_ids"])}
                        for it, a in zip(items, answers, strict=True)
                    ],
                )
            else:
                text = render(cfg["user_prompt"], {n: as_text(t, v) for n, t, v, _ in ins})
                answers = llm(nid, [text])
                if len(answers) != 1:
                    raise StepError(node, "the model answered a different number of items")
                outputs[nid] = ("Value", check_value(node, out["fields"], answers[0]))
        elif kind == "final_report":
            _, port_type, value, src = inputs_of(node)[0]
            # Aggregates read the full classifier output, never the contract-trimmed view.
            if port_type == "Classifications":
                value = outputs[split_ref(node["inputs"][REPORT_INPUT])[0]][1]
            result = build_report(node, src, port_type, value)
    if result is None:
        raise ValueError("no report")
    return result


def _check_output(node: dict, value: Any, known: dict[str, dict]) -> Any:
    out = node["config"]["output"]
    if out["type"] == "Value":
        return check_value(node, out["fields"], value)
    if out["type"] == "Items":
        return check_items(node, value)
    if out["type"] == "Classifications":
        return check_classifications(node, value)
    return check_records(node, value, known)
