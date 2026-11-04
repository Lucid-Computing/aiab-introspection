/**
 * What goes into and comes out of each node, described for people: every input port with
 * what it reads, and the output port with its type and the shape of one value. The node
 * cards and the inspector both draw from this, so they cannot disagree. Pure: no React.
 */
import { INPUT_RULES, inputType, nodeById, outputPort, splitRef } from "./ports";
import type { Classifier, FinalReport, Output, Plan, PlanNode, PortType } from "./types";

export interface InputDesc {
  name: string;
  /** `<node>.<output>` this input reads, or null when not connected. */
  ref: string | null;
  /** The type arriving on this input, or null when unconnected or unresolved. */
  type: PortType | null;
  /** Shape of the value arriving, when known. */
  shape: string | null;
}

export interface OutputDesc {
  name: string;
  /** A port type, or "Report" for the Final Report, which releases rather than emits. */
  type: PortType | "Report";
  /** One line: the shape of a value of this output. */
  shape: string;
  /** Short qualifier shown after the type, e.g. "3 labels" or "4 fields". */
  note?: string;
}

export interface NodeShape {
  inputs: InputDesc[];
  /** Null only for a node with no output, which today is none: the report has a Report pseudo-output. */
  output: OutputDesc | null;
}

/** The labels a classifier can emit, in order, with `abstain` when a threshold is set. */
export function classifierLabelNames(node: Classifier): string[] {
  const names = node.config.labels.map((l) => l.name);
  return node.config.threshold !== undefined ? [...names, "abstain"] : names;
}

/** One classification as the classifier declares it, e.g. `{item_id, label: a | b, score, evidence_ids}`. */
export function classificationShape(node: Classifier): string {
  const labels = classifierLabelNames(node).join(" | ") || "…";
  const parts = ["item_id", `label: ${labels}`];
  if (node.config.output_contract.score) parts.push("score");
  if (node.config.output_contract.evidence_ids) parts.push("evidence_ids");
  return `[{${parts.join(", ")}}]`;
}

/** A Value as declared: `{flagged: boolean, sessions: integer}`. */
export function valueShape(fields: Record<string, string>): string {
  const entries = Object.entries(fields);
  return entries.length ? `{${entries.map(([k, t]) => `${k}: ${t}`).join(", ")}}` : "no fields declared";
}

/** The shape of one value of a port type when nothing more specific is known. */
export function genericShape(type: PortType): string {
  switch (type) {
    case "LogRecords":
      return "[{agent_id, session_id, kind, content, …}]";
    case "Items":
      return "[{id, text, record_ids}]";
    case "Classifications":
      return "[{item_id, label, score, evidence_ids}]";
    case "Value":
      return "{…}";
  }
}

function declaredShape(out: Output): string {
  return out.type === "Value" ? valueShape(out.fields) : genericShape(out.type);
}

/** What the report releases: its fields as `{name: type, …}`. */
export function reportShape(node: FinalReport): string {
  return node.config.fields.length
    ? `{${node.config.fields.map((f) => `${f.name}: ${f.type}`).join(", ")}}`
    : "no fields yet";
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** The output of a node as a description, independent of what it is connected to. */
export function describeOutput(node: PlanNode): OutputDesc | null {
  switch (node.type) {
    case "input_logs":
      return { name: "records", type: "LogRecords", shape: genericShape("LogRecords") };
    case "classifier":
      return {
        name: "results",
        type: "Classifications",
        shape: classificationShape(node),
        note: plural(node.config.labels.length, "label"),
      };
    case "run_python":
    case "run_llm": {
      const out = node.config.output;
      const fields = Object.keys(out.fields).length;
      return {
        name: out.name,
        type: out.type,
        shape: declaredShape(out),
        note: out.type === "Value" ? plural(fields, "field") : undefined,
      };
    }
    case "final_report":
      return {
        name: "releases",
        type: "Report",
        shape: reportShape(node),
        note: plural(node.config.fields.length, "field"),
      };
  }
}

/** The shape of what a given output carries, seen from a consumer. */
export function shapeOfRef(plan: Plan, ref: string): string | null {
  const [src, port] = splitRef(ref);
  const source = nodeById(plan).get(src);
  const out = source && outputPort(source);
  if (!source || !out || out.name !== port) return null;
  return describeOutput(source)?.shape ?? null;
}

export function describeInputs(plan: Plan, node: PlanNode): InputDesc[] {
  const rule = INPUT_RULES[node.type];
  const names = rule.kind === "fixed" ? rule.ports : rule.kind === "dynamic" ? Object.keys(node.inputs) : [];
  return names.map((name) => {
    const ref = node.inputs[name] ?? null;
    return {
      name,
      ref,
      type: ref ? inputType(plan, node, name) : null,
      shape: ref ? shapeOfRef(plan, ref) : null,
    };
  });
}

export function describeNode(plan: Plan, node: PlanNode): NodeShape {
  return { inputs: describeInputs(plan, node), output: describeOutput(node) };
}
