/**
 * A block as the Python file it will become (MAC-468): INPUT, OUTPUT, SETTINGS, then the
 * function. For the fixed blocks the body mirrors what the box runs today
 * (static/aiab/baked/graph.py: select_records, to_items, the classifier and run_llm branches
 * of run_plan, build_report; rule_holds, filter_rules and filter_llm for a Filter). Read only:
 * the plan is still plan.json, and this text is drawn from it.
 */
import { describeNode } from "./shape";
import { outputSpec } from "./spec";
import { type Plan, type PlanNode, RECORD_KINDS } from "./types";
import { outputPort } from "./ports";
import { topoOrder } from "./validate";

/** A JSON value as a Python literal, indented like a settings block. */
function py(value: unknown, indent = 0): string {
  const pad = "    ".repeat(indent + 1);
  const end = "    ".repeat(indent);
  if (value === null || value === undefined) return "None";
  if (value === true) return "True";
  if (value === false) return "False";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) {
    if (value.every((v) => typeof v !== "object" || v === null)) return `[${value.map((v) => py(v)).join(", ")}]`;
    return `[\n${value.map((v) => pad + py(v, indent + 1)).join(",\n")},\n${end}]`;
  }
  const entries = Object.entries(value as Record<string, unknown>).filter(([, v]) => v !== undefined);
  if (!entries.length) return "{}";
  return `{\n${entries.map(([k, v]) => `${pad}${JSON.stringify(k)}: ${py(v, indent + 1)}`).join(",\n")},\n${end}}`;
}

const BODY: Record<PlanNode["type"], string> = {
  input_logs: `def run(records, settings):
    """Keep the records of the sessions this agreement names; none named means all."""
    sessions = set(settings["sessions"])
    return [r for r in records if not sessions or r["session_id"] in sessions]`,
  classifier: `def items(records, settings):
    """Query format: cut the records into the texts the model is asked about."""
    if settings["per"] == "record":
        return [{"id": r["id"], "text": r["content"], "record_ids": [r["id"]]} for r in records]
    sessions = {}
    for r in records:
        sessions.setdefault(r["session_id"], []).append(r)
    out = []
    for sid, rs in sessions.items():
        rs = sorted(rs, key=lambda r: (r["batch"], r["offset"]))
        for k in range(0, len(rs), settings["window"]):
            chunk = rs[k : k + settings["window"]]
            out.append({
                "id": sid if len(rs) <= settings["window"] else f"{sid}#{k // settings['window'] + 1}",
                "text": "\\n".join(f"{r['kind']}: {r['content']}" for r in chunk),
                "record_ids": [r["id"] for r in chunk],
            })
    return out


def prompts(items, settings):
    """One question per item. The box sends these to the model; this code cannot."""
    return [it["text"] for it in items]


def parse(items, answers, settings):
    """One label and score per item. Below the threshold the label is abstain."""
    results = []
    for it, a in zip(items, answers, strict=True):
        label = a["label"]
        if settings["threshold"] is not None and a["score"] < settings["threshold"]:
            label = "abstain"
        results.append({
            "item_id": it["id"],
            "label": label,
            "score": float(a["score"]),
            "evidence_ids": list(it["record_ids"]),
        })
    return results`,
  filter_logs: "",
  combine_logs: `def run(a, b, settings):
    """and: the records in both a and b; or: the records in either. Matched by record id,
    each kept once, in the order of the run's records."""
    in_a, in_b = {r["id"] for r in a}, {r["id"] for r in b}
    keep = in_a & in_b if settings["op"] == "and" else in_a | in_b
    given = {r["id"]: r for r in a + b}
    return [given[i] for i in sorted(keep, key=position_in_the_run)]`,
  run_python: "",
  run_llm: `def prompts(inputs, settings):
    """The text sent to the model: the user prompt with each {{name}} filled in. With an
    Items output there is one prompt per item, otherwise one in total."""
    return [render(settings["user_prompt"], values) for values in inputs]


def parse(answers, settings):
    """The model's answer, checked by the box against OUTPUT before any block reads it."""
    return answers`,
  final_report: `def run(result, settings):
    """The only values that leave the box. The box checks each against its declared type
    and the byte cap after this returns."""
    report = {}
    for f in settings["fields"]:
        x = read(result, f.get("from", f["name"]))  # count.<label>, any.<label>, proportion.<label> or a field
        if f["type"] == "integer" and 0 < x < settings["min_count"]:
            x = 0  # a small count would single out a session
        report[f["name"]] = x
    return report`,
};

const FILTER_BODY = {
  rules: `def run(records, settings):
    """Keep the records where all (or any) of the rules hold. With exclude, drop them instead."""
    test = all if settings["match"] == "all" else any
    return [
        r for r in records
        if test(holds(rule, r) for rule in settings["rules"]) != settings["exclude"]
    ]


def holds(rule, r):
    """One rule on one record. Text compares in lower case unless the rule is case sensitive;
    a regex is the box's own subset and matches anywhere; times are UTC; a length counts
    characters."""
    field, op, value = rule["field"], rule["op"], rule["value"]
    if field == "content":
        text = r["content"]
        if op == "regex":
            return search(value, text, rule["case_sensitive"]) is not None
        if not rule["case_sensitive"]:
            text, value = text.lower(), value.lower()
        if op == "contains":
            return value in text
        if op == "not_contains":
            return value not in text
        if op == "equals":
            return text == value
        if op == "starts_with":
            return text.startswith(value)
        return text.endswith(value)
    if field == "kind":
        return r["kind"] in value
    if field in ("agent", "session"):
        found = r["agent_id" if field == "agent" else "session_id"] in value
        return found if op == "in" else not found
    if field == "time":
        ts = datetime.fromisoformat(r["ts"])
        if op == "between":
            return datetime.fromisoformat(value[0]) <= ts < datetime.fromisoformat(value[1])
        at = datetime.fromisoformat(value)
        return ts < at if op == "before" else ts >= at
    return len(r["content"]) >= value if op == "at_least" else len(r["content"]) <= value`,
  llm: `def items(records, settings):
    """One question per record, or per whole session."""
    if settings["judge"] == "record":
        return [{"id": r["id"], "text": r["content"], "record_ids": [r["id"]]} for r in records]
    sessions = {}
    for r in records:
        sessions.setdefault(r["session_id"], []).append(r)
    out = []
    for sid, rs in sessions.items():
        rs = sorted(rs, key=lambda r: (r["batch"], r["offset"]))
        out.append({
            "id": sid,
            "text": "\\n".join(f"{r['kind']}: {r['content']}" for r in rs),
            "record_ids": [r["id"] for r in rs],
        })
    return out


def prompts(items, settings):
    """The question, asked about each item: answer yes or no. The box sends these to the
    model; this code cannot."""
    return [it["text"] for it in items]


def parse(records, items, answers, settings):
    """Yes keeps the item's records, no drops them. Below the threshold the answer is
    unsure, and settings["unsure"] decides."""
    keep = set()
    for it, a in zip(items, answers, strict=True):
        unsure = settings["threshold"] is not None and a["score"] < settings["threshold"]
        if (settings["unsure"] == "keep") if unsure else a["label"] == "yes":
            keep.update(it["record_ids"])
    return [r for r in records if r["id"] in keep]`,
};

const NARROWED_BODY = `def run(records, settings):
    """Keep the records this agreement may read: its sessions, agents, kinds and keywords.
    The box refuses a run whose records fall outside the agents or the time window."""
    sessions = set(settings["sessions"])
    kinds = set(settings["kinds"])
    keywords = [k.lower() for k in settings["keywords"] if k]
    agents = set(settings["agents"])
    kept = []
    for r in records:
        if r["kind"] not in kinds or (agents and r["agent_id"] not in agents):
            continue
        if sessions and r["session_id"] not in sessions:
            continue
        if keywords and not any(k in r["content"].lower() for k in keywords):
            continue
        kept.append(r)
    return kept`;

/** The filters a Log Source still carries from before the pane was simplified. */
export function narrowing(node: PlanNode): string[] {
  if (node.type !== "input_logs") return [];
  const c = node.config;
  const out: string[] = [];
  if (c.input_scope.agents.length) out.push("agents");
  if (c.input_scope.window_start || c.input_scope.window_end) out.push("a time window");
  if (c.filter.kinds.length !== RECORD_KINDS.length) out.push("record kinds");
  if (c.filter.keywords.length) out.push("keywords");
  return out;
}

function settingsOf(node: PlanNode): Record<string, unknown> {
  switch (node.type) {
    case "input_logs": {
      const c = node.config;
      const base = { source: c.source, sessions: c.filter.sessions ?? [] };
      if (!narrowing(node).length) return base;
      return {
        ...base,
        agents: c.input_scope.agents,
        window_start: c.input_scope.window_start ?? null,
        window_end: c.input_scope.window_end ?? null,
        kinds: c.filter.kinds,
        keywords: c.filter.keywords,
      };
    }
    case "filter_logs": {
      const c = node.config;
      if (c.mode === "rules") return { match: c.match, exclude: c.exclude, rules: c.rules };
      return {
        model: c.llm.model.id,
        question: c.llm.question,
        judge: c.llm.judge,
        yes_examples: c.llm.yes_examples,
        no_examples: c.llm.no_examples,
        threshold: c.llm.threshold ?? null,
        unsure: c.llm.unsure,
      };
    }
    case "combine_logs":
      return { op: node.config.op };
    case "classifier": {
      const c = node.config;
      return {
        per: c.group_by,
        window: c.window,
        model: c.model.id,
        instructions: c.instructions,
        labels: c.labels,
        threshold: c.threshold ?? null,
      };
    }
    case "run_llm": {
      const c = node.config;
      return { model: c.model.id, system_prompt: c.system_prompt, user_prompt: c.user_prompt };
    }
    case "final_report": {
      const c = node.config;
      return {
        fields: c.fields,
        min_count: c.privacy.min_count,
        max_result_bytes: c.privacy.max_result_bytes,
        allow_free_text: c.privacy.allow_free_text,
      };
    }
    case "run_python":
      return {};
  }
}

/** The block as a Python file. A Code block is its own code, and so is a Query format block. */
export function blockPython(plan: Plan, node: PlanNode): string {
  const { inputs, output } = describeNode(plan, node);
  const bound = inputs.filter((i) => i.ref);
  const input =
    node.type === "input_logs"
      ? py({ records: "LogRecords" })
      : bound.length
        ? py(Object.fromEntries(bound.map((i) => [i.name, i.type ?? "?"])))
        : "None  # N/A, not connected";
  const spec = outputSpec(node);
  const out = output
    ? py({ type: output.type, fields: Object.fromEntries(spec.fields.map((f) => [f.name, f.type])) })
    : "None";
  const head = `# blocks/${node.id}.py\nINPUT = ${input}\nOUTPUT = ${out}\n`;
  if (node.type === "run_python") return `${head}\n${node.config.code.trimEnd()}\n`;
  const body = narrowing(node).length
    ? NARROWED_BODY
    : node.type === "filter_logs"
      ? FILTER_BODY[node.config.mode]
      : BODY[node.type];
  return `${head}SETTINGS = ${py(settingsOf(node))}\n\n\n${body}\n`;
}

/**
 * The wrapper: only what connects to what. One call per block in the order the box runs
 * them; a block's result is named after its output, and passed to the blocks that read it.
 */
export function planPython(plan: Plan): string {
  let order: string[];
  try {
    order = topoOrder(plan);
  } catch {
    order = plan.nodes.map((n) => n.id);
  }
  const byId = new Map(plan.nodes.map((n) => [n.id, n]));
  const names = plan.nodes.map((n) => outputPort(n)?.name).filter(Boolean);
  // The result's name: the output's name, or <block>_<output> when two blocks share one.
  const result = (id: string) => {
    const name = outputPort(byId.get(id)!)?.name ?? id;
    return names.filter((x) => x === name).length > 1 ? `${id}_${name}` : name;
  };
  const used = order.filter((id) => byId.has(id));
  const lines = used.map((id) => {
    const n = byId.get(id)!;
    const args = Object.entries(n.inputs)
      .map(([name, ref]) => {
        const src = ref.split(".")[0];
        return byId.has(src) ? `${name}=${result(src)}` : `${name}=None`;
      })
      .join(", ");
    return n.type === "final_report" ? `${id}(${args})` : `${result(id)} = ${id}(${args})`;
  });
  return `# plan.py\nfrom blocks import ${used.join(", ") || "()"}\n\n${lines.join("\n")}\n`;
}
