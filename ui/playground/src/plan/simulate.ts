/**
 * Deterministic mock run of a plan over the synthetic logs. Nothing is executed: Python
 * source is never run and no model is called. Every value below comes from fixed rules
 * (keyword overlap, counts, a string hash), so the same plan always gives the same output.
 */
import { selectRecords, type LogRecord } from "./mockLogs";
import { splitRef } from "./ports";
import { classifierLabels, resolveReportPath } from "./report";
import type { Classifier, FieldType, FinalReport, Output, Plan, PlanNode } from "./types";
import { topoOrder } from "./validate";

export interface Item {
  id: string;
  text: string;
  record_ids: string[];
}
export interface Classification {
  item_id: string;
  label: string;
  score: number;
  evidence_ids: string[];
}
export type SimValue =
  | { type: "LogRecords"; value: LogRecord[] }
  | { type: "Items"; value: Item[] }
  | { type: "Classifications"; value: Classification[]; flagLabels: string[] }
  | { type: "Value"; value: Record<string, boolean | number | string> };

export interface Simulation {
  outputs: Record<string, SimValue>;
  report: Record<string, boolean | number | string | null> | null;
  reportBytes: number;
  notes: string[];
}

/** FNV-1a, scaled to [0, 1). */
export function hash01(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0) / 2 ** 32;
}

const STOP = new Set(["about", "their", "there", "these", "this", "that", "with", "from", "into", "other", "work"]);
function stems(text: string): string[] {
  return (text.toLowerCase().match(/[a-z]{4,}/g) ?? []).filter((w) => !STOP.has(w)).map((w) => w.slice(0, 5));
}

const round = (x: number) => Math.round(x * 1000) / 1000;

function toItems(v: SimValue, groupBy: "session" | "record", window: number): Item[] {
  if (v.type === "Items") return v.value;
  if (v.type !== "LogRecords") return [];
  if (groupBy === "record") return v.value.map((r) => ({ id: r.id, text: r.content, record_ids: [r.id] }));
  const bySession = new Map<string, LogRecord[]>();
  for (const r of v.value) bySession.set(r.session_id, [...(bySession.get(r.session_id) ?? []), r]);
  const items: Item[] = [];
  for (const [sid, rs] of bySession)
    for (let k = 0; k < rs.length; k += window) {
      const chunk = rs.slice(k, k + window);
      items.push({
        id: rs.length > window ? `${sid}#${k / window + 1}` : sid,
        text: chunk.map((r) => `${r.kind}: ${r.content}`).join("\n"),
        record_ids: chunk.map((r) => r.id),
      });
    }
  return items;
}

function classify(node: Classifier, items: Item[]): Classification[] {
  const labels = node.config.labels;
  return items.map((item) => {
    const words = new Set(stems(item.text));
    const scored = labels
      .map((l) => {
        const keys = [...new Set(stems(`${l.name.replace(/_/g, " ")} ${l.description}`))];
        const hits = keys.filter((k) => words.has(k)).length;
        return { label: l.name, raw: hits + (l.flag ? 0 : 0.5) + hash01(`${node.id}:${item.id}:${l.name}`) * 0.3 };
      })
      .sort((a, b) => b.raw - a.raw);
    const [best, second] = scored;
    const score = best ? round(0.5 + 0.45 * (best.raw / (best.raw + (second?.raw ?? 0) + 0.5))) : 0;
    const label =
      best && (node.config.threshold === undefined || score >= node.config.threshold) ? best.label : "abstain";
    // Evidence: the records of this item; for record-level items that is the record itself.
    return { item_id: item.id, label, score, evidence_ids: item.record_ids.slice(0, 3) };
  });
}

/** Stats used to fill declared Value fields: matched to an input by name, else overall. */
interface Stat {
  name: string;
  count: number;
  flagged: number;
}
function statsOf(name: string, v: SimValue): Stat {
  if (v.type === "Classifications")
    return { name, count: v.value.length, flagged: v.value.filter((c) => v.flagLabels.includes(c.label)).length };
  if (v.type === "Value") {
    const nums = Object.values(v.value);
    return {
      name,
      count: nums.filter((x) => typeof x === "number").length,
      flagged: nums.filter((x) => x === true).length,
    };
  }
  return { name, count: v.value.length, flagged: 0 };
}

function fillValue(
  node: PlanNode,
  out: Output,
  inputs: [string, SimValue][],
): Record<string, boolean | number | string> {
  const stats = inputs.map(([k, v]) => statsOf(k, v));
  const pick = (field: string): Stat => {
    const named = stats.find((s) => field.includes(s.name) || s.name.includes(field.split("_")[0]));
    if (named) return named;
    return stats.reduce(
      (a, s) => ({ name: "all", count: Math.max(a.count, s.count), flagged: a.flagged + s.flagged }),
      {
        name: "all",
        count: 0,
        flagged: 0,
      },
    );
  };
  const fill = (field: string, t: FieldType): boolean | number | string => {
    const s = pick(field);
    if (t === "boolean") return s.flagged > 0;
    if (t === "integer") return s.count;
    if (t === "number") return s.count ? round(Math.min(1, s.flagged / s.count)) : round(hash01(`${node.id}:${field}`));
    return `[simulated ${field}]`;
  };
  return Object.fromEntries(Object.entries(out.fields).map(([f, t]) => [f, fill(f, t)]));
}

function runNode(node: PlanNode, inputs: [string, SimValue][], notes: string[]): SimValue | null {
  switch (node.type) {
    case "input_logs":
      return { type: "LogRecords", value: selectRecords(node) };
    case "classifier": {
      const src = inputs[0]?.[1];
      const items = src ? toItems(src, node.config.group_by, node.config.window) : [];
      return {
        type: "Classifications",
        value: classify(node, items),
        flagLabels: node.config.labels.filter((l) => l.flag).map((l) => l.name),
      };
    }
    case "run_python":
    case "run_llm": {
      const out = node.config.output;
      const kind = node.type === "run_python" ? "Python not executed" : "no model called";
      if (out.type === "Value") {
        notes.push(`${node.id}: ${kind}; declared fields filled from upstream counts.`);
        return { type: "Value", value: fillValue(node, out, inputs) };
      }
      if (out.type === "Items") {
        const list = inputs.find(([, v]) => v.type === "Items" || v.type === "LogRecords")?.[1];
        const items = list ? toItems(list, "session", 1000) : [];
        notes.push(
          node.type === "run_python"
            ? `${node.id}: Python not executed; records grouped into one item per session.`
            : `${node.id}: no model called; each item's output is a placeholder echoing its input.`,
        );
        return {
          type: "Items",
          value:
            node.type === "run_python"
              ? items
              : items.map((it) => ({
                  ...it,
                  text: `[simulated ${out.name}] ${it.text.replace(/\n/g, " ").slice(0, 160)}`,
                })),
        };
      }
      if (out.type === "LogRecords") {
        notes.push(`${node.id}: ${kind}; records passed through unchanged.`);
        const recs = inputs.find(([, v]) => v.type === "LogRecords")?.[1];
        return { type: "LogRecords", value: recs?.type === "LogRecords" ? recs.value : [] };
      }
      notes.push(`${node.id}: ${kind}; upstream classifications passed through.`);
      const cls = inputs.filter(([, v]) => v.type === "Classifications").map(([, v]) => v) as Extract<
        SimValue,
        { type: "Classifications" }
      >[];
      return {
        type: "Classifications",
        value: cls.flatMap((c) => c.value),
        flagLabels: cls.flatMap((c) => c.flagLabels),
      };
    }
    case "final_report":
      return null;
  }
}

function reportValue(v: SimValue, path: string[], labels: string[] | null): boolean | number | string | null {
  if (v.type === "Value") return v.value[path[0]] ?? null;
  if (v.type !== "Classifications") return null;
  const [head, label] = path;
  const n = v.value.length;
  const withLabel = v.value.filter((c) => c.label === label).length;
  if (head === "count") return label === undefined ? n : withLabel;
  if (head === "max_score") return n ? Math.max(...v.value.map((c) => c.score)) : 0;
  if (head === "proportion") return n ? round(withLabel / n) : 0;
  if (head === "any") return withLabel > 0 && (!labels || labels.includes(label));
  return null;
}

export function buildReport(plan: Plan, report: FinalReport, outputs: Record<string, SimValue>) {
  const { min_count } = report.config.privacy;
  const suppressed: string[] = [];
  const values = Object.fromEntries(
    report.config.fields.map((f) => {
      if (!resolveReportPath(plan, report, f.from).ok) return [f.name, null];
      const [input, ...path] = f.from.split(".");
      const src = splitRef(report.inputs[input])[0];
      const v = outputs[src];
      const node = plan.nodes.find((n) => n.id === src);
      let x = v ? reportValue(v, path, classifierLabels(node)) : null;
      if (f.type === "integer" && typeof x === "number" && x > 0 && x < min_count) {
        suppressed.push(f.name);
        x = 0;
      }
      return [f.name, x];
    }),
  );
  return { values, suppressed };
}

export function simulate(plan: Plan): Simulation {
  const outputs: Record<string, SimValue> = {};
  const notes: string[] = [];
  let report: Simulation["report"] = null;
  for (const id of topoOrder(plan)) {
    const node = plan.nodes.find((n) => n.id === id)!;
    const inputs = Object.entries(node.inputs)
      .map(([k, r]) => [k, outputs[splitRef(r)[0]]] as [string, SimValue | undefined])
      .filter((p): p is [string, SimValue] => !!p[1]);
    if (node.type === "final_report") {
      const r = buildReport(plan, node, outputs);
      report = r.values;
      if (r.suppressed.length)
        notes.push(
          `${node.id}: counts below min_count (${node.config.privacy.min_count}) released as 0: ${r.suppressed.join(", ")}.`,
        );
      continue;
    }
    const v = runNode(node, inputs, notes);
    if (v) outputs[id] = v;
  }
  const reportBytes = report ? new TextEncoder().encode(JSON.stringify(report)).length : 0;
  return { outputs, report, reportBytes, notes };
}
