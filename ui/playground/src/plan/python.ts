/**
 * A block as the Python file it will become (MAC-468): INPUT, OUTPUT, SETTINGS, then the
 * function. For the fixed blocks the body mirrors what the box runs today
 * (static/aiab/baked/graph.py: select_records, to_items, the classifier and run_llm branches
 * of run_plan, build_report). Read only: the plan is still plan.json, and this text is drawn
 * from it.
 */
import { describeNode } from "./shape";
import type { Plan, PlanNode } from "./types";
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
    """Keep the records this agreement may read. The box refuses a run whose records fall
    outside the agents or the time window."""
    kinds = set(settings["kinds"])
    keywords = [k.lower() for k in settings["keywords"] if k]
    agents = set(settings["agents"])
    kept = []
    for r in records:
        if r["kind"] not in kinds or (agents and r["agent_id"] not in agents):
            continue
        if keywords and not any(k in r["content"].lower() for k in keywords):
            continue
        kept.append(r)
    if len(kept) > settings["max_records"]:
        raise ValueError("more records than max_records")
    return kept`,
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

function settingsOf(node: PlanNode): Record<string, unknown> {
  switch (node.type) {
    case "input_logs": {
      const c = node.config;
      return {
        source: c.source,
        agents: c.input_scope.agents,
        window_start: c.input_scope.window_start ?? null,
        window_end: c.input_scope.window_end ?? null,
        kinds: c.filter.kinds,
        keywords: c.filter.keywords,
        max_records: c.max_records,
      };
    }
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
  const out = output ? py({ type: output.type, shape: output.shape }) : "None";
  const head = `# blocks/${node.id}.py\nINPUT = ${input}\nOUTPUT = ${out}\n`;
  if (node.type === "run_python") return `${head}\n${node.config.code.trimEnd()}\n`;
  return `${head}SETTINGS = ${py(settingsOf(node))}\n\n\n${BODY[node.type]}\n`;
}

/** The wrapper: one call per block, in the order the box runs them. */
export function planPython(plan: Plan): string {
  let order: string[];
  try {
    order = topoOrder(plan);
  } catch {
    order = plan.nodes.map((n) => n.id);
  }
  const byId = new Map(plan.nodes.map((n) => [n.id, n]));
  const lines = order.flatMap((id) => {
    const n = byId.get(id);
    if (!n) return [];
    const args = Object.values(n.inputs)
      .map((ref) => ref.split(".")[0])
      .join(", ");
    return [n.type === "final_report" ? `${n.id}(${args})` : `${n.id} = ${n.id}(${args})`];
  });
  return `# plan.py\n${lines.join("\n")}\n`;
}
