/**
 * The Filter and AND / OR blocks: which rule values the box accepts (the same checks and
 * messages as `rule_issue` in static/aiab/baked/plan_ir.py), how a rule reads, and what
 * the blocks keep from the playground's sample logs (`rule_holds`, `filter_rules` and the
 * combine_logs branch in static/aiab/baked/graph.py). Sample logs are synthetic; real
 * records are filtered in the box only.
 */
import { type LogRecord, selectRecords, sourceRecords } from "./mockLogs";
import { compilePattern, patternIssue } from "./pattern";
import { splitRef } from "./ports";
import {
  type CombineLogs,
  FIELD_OPS,
  type FilterLogs,
  type FilterRules,
  type Plan,
  type PlanNode,
  RECORD_KINDS,
  type Rule,
  type RuleField,
} from "./types";

export const FIELD_NAMES: Record<RuleField, string> = {
  content: "text",
  kind: "record kind",
  agent: "agent",
  session: "session",
  time: "time",
  length: "length",
};

/** How a field and an operator read in the settings pane. */
export const FIELD_LABELS: Record<RuleField, string> = {
  content: "Text",
  kind: "Record kind",
  agent: "Agent",
  session: "Session",
  time: "Time",
  length: "Length",
};
export const OP_LABELS: Record<string, string> = {
  contains: "contains",
  not_contains: "does not contain",
  equals: "is exactly",
  starts_with: "starts with",
  ends_with: "ends with",
  regex: "matches regex",
  in: "is one of",
  not_in: "is not one of",
  between: "between",
  before: "before",
  after: "after",
  at_least: "at least",
  at_most: "at most",
};

const AGENT_RE = /^[A-Za-z0-9_-]{1,128}$/;
const UTC_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;

/** A UTC time the box can read: a real date, year 1 or later (Python's datetime). */
function isDate(t: string): boolean {
  const d = new Date(t);
  return !Number.isNaN(d.getTime()) && Number(t.slice(0, 4)) >= 1 && `${d.toISOString().slice(0, 19)}Z` === t;
}

/** Why the box refuses this rule, or null. Same order and words as the box. */
export function ruleIssue(rule: Rule): string | null {
  const { field, op } = rule;
  const value: unknown = rule.value;
  if (!(FIELD_OPS[field] as readonly string[]).includes(op)) return `"${op}" does not apply to ${FIELD_NAMES[field]}`;
  if (field !== "content" && "case_sensitive" in rule) return "only a text rule is case sensitive";
  if (field === "content") {
    if (typeof value !== "string") return "the text to match is a string";
    if (!value) return "write the text to match";
    if (op === "regex") {
      const why = patternIssue(value);
      return why ? `regex: ${why}` : null;
    }
    return null;
  }
  if (field === "length") {
    if (typeof value !== "number" || !Number.isInteger(value) || value < 0)
      return "a length is a whole number of characters, 0 or more";
    return null;
  }
  if (field === "time") {
    const times: unknown[] = op === "between" ? (Array.isArray(value) ? value : []) : [value];
    if (op === "between" && (!Array.isArray(value) || value.length !== 2)) return "between takes two times";
    for (const t of times) {
      if (typeof t !== "string" || !UTC_RE.test(t)) return "write times in UTC as YYYY-MM-DDTHH:MM:SSZ";
      if (!isDate(t)) return `${t} is not a date`;
    }
    if (op === "between" && (times[0] as string) >= (times[1] as string)) return "the start comes before the end";
    return null;
  }
  if (!Array.isArray(value) || !value.length) return `pick at least one ${FIELD_NAMES[field]}`;
  if (new Set(value.map(String)).size !== value.length || value.some((v) => typeof v !== "string"))
    return `each ${FIELD_NAMES[field]} once`;
  const values = value as string[];
  if (field === "kind" && values.some((v) => !(RECORD_KINDS as readonly string[]).includes(v)))
    return `record kinds are ${RECORD_KINDS.join(", ")}`;
  if (field === "agent" && values.some((v) => !AGENT_RE.test(v)))
    return "an agent id is letters, digits, _ and -, up to 128";
  if (field === "session" && values.some((v) => [...v].length < 1 || [...v].length > 256))
    return "a session id is 1 to 256 characters";
  return null;
}

/** A rule that is only unfinished (nothing written or picked yet), not wrong. */
export function ruleUnfinished(why: string): boolean {
  return why === "write the text to match" || why.startsWith("pick at least one");
}

/** A new rule of this field: its first operator and an empty value. */
export function newRule(field: RuleField, now = new Date()): Rule {
  const day = `${now.toISOString().slice(0, 10)}T00:00:00Z`;
  switch (field) {
    case "content":
      return { field, op: "contains", value: "", case_sensitive: false };
    case "kind":
      return { field, op: "in", value: [] };
    case "agent":
      return { field, op: "in", value: [] };
    case "session":
      return { field, op: "in", value: [] };
    case "time":
      return { field, op: "after", value: day };
    case "length":
      return { field, op: "at_least", value: 1000 };
  }
}

/** The rule with another operator, its value kept where it still fits. */
export function withOp(rule: Rule, op: string): Rule {
  if (rule.field !== "time") return { ...rule, op } as Rule;
  const first = Array.isArray(rule.value) ? (rule.value[0] ?? "") : rule.value;
  if (op !== "between") return { ...rule, op: op as "before" | "after", value: first };
  if (Array.isArray(rule.value)) return { ...rule, op: "between", value: rule.value };
  const end = isDate(first) ? `${new Date(Date.parse(first) + 86_400_000).toISOString().slice(0, 19)}Z` : "";
  return { ...rule, op: "between", value: [first, end] };
}

const quote = (s: string) => (s.length > 24 ? `"${s.slice(0, 23)}…"` : `"${s}"`);

/** One rule in a few words: `text contains "api_key"`, `kind is tool_call`. */
export function ruleText(rule: Rule): string {
  const op = OP_LABELS[rule.op];
  switch (rule.field) {
    case "content":
      return rule.op === "regex" ? `text matches /${rule.value}/` : `text ${op} ${quote(rule.value)}`;
    case "kind":
    case "agent":
    case "session": {
      const word = rule.field;
      const not = rule.op === "not_in" ? " not" : "";
      return rule.value.length === 1
        ? `${word} is${not} ${rule.value[0]}`
        : `${word} is${not} one of ${rule.value.join(", ") || "…"}`;
    }
    case "time": {
      const t = (s: string) => s.replace("T", " ").replace(/:00Z$|Z$/, "");
      return Array.isArray(rule.value)
        ? `time between ${t(rule.value[0] ?? "")} and ${t(rule.value[1] ?? "")}`
        : `time ${op} ${t(rule.value)}`;
    }
    case "length":
      return `length ${op} ${rule.value}`;
  }
}

/** The block's one line: what it keeps, in a few words. */
export function filterLine(node: FilterLogs): string {
  const c = node.config;
  if (c.mode === "llm") {
    const q = c.llm.question.trim();
    return q ? `per ${c.llm.judge}: ${q}` : "no question yet";
  }
  if (!c.rules.length) return "no rules yet";
  const verb = c.exclude ? "drop" : "keep";
  const more = c.rules.length > 1 ? ` +${c.rules.length - 1} (${c.match})` : "";
  return `${verb}: ${ruleText(c.rules[0])}${more}`;
}

export function combineLine(node: CombineLogs): string {
  return node.config.op === "and" ? "records in both A and B" : "records in A or B";
}

// ---- the sample count ------------------------------------------------------------------

/** One rule on one record, as the box decides it (graph.py rule_holds). */
export function ruleHolds(rule: Rule, r: LogRecord, re?: RegExp): boolean {
  switch (rule.field) {
    case "content": {
      if (rule.op === "regex") return (re ?? compilePattern(rule.value, rule.case_sensitive)).test(r.content);
      const cs = rule.case_sensitive;
      const text = cs ? r.content : r.content.toLowerCase();
      const value = cs ? rule.value : rule.value.toLowerCase();
      if (rule.op === "contains") return text.includes(value);
      if (rule.op === "not_contains") return !text.includes(value);
      if (rule.op === "equals") return text === value;
      if (rule.op === "starts_with") return text.startsWith(value);
      return text.endsWith(value);
    }
    case "kind":
      return (rule.value as string[]).includes(r.kind);
    case "agent":
    case "session": {
      const found = rule.value.includes(rule.field === "agent" ? r.agent_id : r.session_id);
      return rule.op === "in" ? found : !found;
    }
    case "time": {
      const ts = Date.parse(r.ts);
      if (Array.isArray(rule.value)) return Date.parse(rule.value[0]) <= ts && ts < Date.parse(rule.value[1]);
      return rule.op === "before" ? ts < Date.parse(rule.value) : ts >= Date.parse(rule.value);
    }
    case "length": {
      const size = [...r.content].length;
      return rule.op === "at_least" ? size >= rule.value : size <= rule.value;
    }
  }
}

export function applyRules(c: FilterRules, records: LogRecord[]): LogRecord[] {
  const compiled = c.rules.map((r) => (r.field === "content" && r.op === "regex" ? compilePattern(r.value, r.case_sensitive) : undefined));
  return records.filter((rec) => {
    const hits = c.rules.map((rule, k) => () => ruleHolds(rule, rec, compiled[k]));
    const hit = c.match === "all" ? hits.every((h) => h()) : hits.some((h) => h());
    return hit !== c.exclude;
  });
}

/** Why the sample logs cannot stand in for this block's records, or null when they can. */
function uncountable(node: FilterLogs, sample: LogRecord[]): string | null {
  const c = node.config;
  if (c.mode === "llm") return "the model decides in the box";
  if (!c.rules.length || c.rules.some((r) => ruleIssue(r))) return "the rules are not finished";
  const agents = new Set(sample.map((r) => r.agent_id));
  const sessions = new Set(sample.map((r) => r.session_id));
  for (const r of c.rules)
    if ((r.field === "agent" && r.value.some((v) => !agents.has(v))) || (r.field === "session" && r.value.some((v) => !sessions.has(v))))
      return `the sample logs have none of these ${r.field}s`;
  return null;
}

/**
 * The sample records that reach the output of `id`, in order, or null when the sample cannot
 * say (a model decides, a rule names agents or sessions the sample does not have, or a Code
 * block sits upstream). The Input filter's records come from `selectRecords`.
 */
export function sampleRecords(plan: Plan, id: string): LogRecord[] | null {
  const byId = new Map(plan.nodes.map((n) => [n.id, n]));
  const source = plan.nodes.find((n) => n.type === "input_logs");
  const sample = source?.type === "input_logs" ? sourceRecords(source.config.source) : [];
  const memo = new Map<string, LogRecord[] | null>();
  const reach = (nid: string): LogRecord[] | null => {
    if (memo.has(nid)) return memo.get(nid)!;
    const n = byId.get(nid);
    let out: LogRecord[] | null = null;
    const read = (node: PlanNode, port: string) => (node.inputs[port] ? reach(splitRef(node.inputs[port])[0]) : null);
    if (n?.type === "input_logs") out = selectRecords(n);
    else if (n?.type === "filter_logs") {
      const input = read(n, "records");
      out = input && n.config.mode === "rules" && !uncountable(n, sample) ? applyRules(n.config, input) : null;
    } else if (n?.type === "combine_logs") {
      const a = read(n, "a");
      const b = read(n, "b");
      if (a && b) {
        const inB = new Set(b.map((r) => r.id));
        const inA = new Set(a.map((r) => r.id));
        const keep = new Set(n.config.op === "and" ? a.filter((r) => inB.has(r.id)).map((r) => r.id) : [...inA, ...inB]);
        // The run's record order: the order the sample source lists them.
        out = sample.filter((r) => keep.has(r.id));
      }
    }
    memo.set(nid, out);
    return out;
  };
  return reach(id);
}

export interface SampleCount {
  kept: number;
  of: number;
  sessions: number;
}

/** "Keeps 9 of 28 records · 3 sessions" for a Filter, from the sample logs; or why not. */
export function sampleCount(plan: Plan, node: FilterLogs): SampleCount | { why: string } {
  const source = plan.nodes.find((n) => n.type === "input_logs");
  const sample = source?.type === "input_logs" ? sourceRecords(source.config.source) : [];
  const own = uncountable(node, sample);
  if (own) return { why: own };
  const ref = node.inputs.records;
  const input = ref ? sampleRecords(plan, splitRef(ref)[0]) : null;
  if (!input) return { why: ref ? "a block above it is decided in the box" : "nothing is connected" };
  const kept = applyRules(node.config as FilterRules, input);
  return { kept: kept.length, of: input.length, sessions: new Set(kept.map((r) => r.session_id)).size };
}
