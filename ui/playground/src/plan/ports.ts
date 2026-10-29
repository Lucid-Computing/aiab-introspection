/** Typed ports: what each node type emits and accepts, and the one compatibility table. */
import type { NodeType, Plan, PlanNode, PortType } from "./types";

export interface OutputPort {
  name: string;
  type: PortType;
}

/** The single output of a node, or null for the Final Report. */
export function outputPort(node: PlanNode): OutputPort | null {
  switch (node.type) {
    case "input_logs":
      return { name: "records", type: "LogRecords" };
    case "classifier":
      return { name: "results", type: "Classifications" };
    case "run_python":
    case "run_llm":
      return { name: node.config.output.name, type: node.config.output.type };
    case "final_report":
      return null;
  }
}

/**
 * Input ports. `fixed` nodes have exactly the listed inputs; `dynamic` nodes declare any
 * number of named inputs, each created by connecting an edge or writing a binding.
 */
export const INPUT_RULES: Record<
  NodeType,
  { kind: "none" } | { kind: "fixed"; ports: string[] } | { kind: "dynamic" }
> = {
  input_logs: { kind: "none" },
  classifier: { kind: "fixed", ports: ["items"] },
  run_python: { kind: "dynamic" },
  run_llm: { kind: "dynamic" },
  final_report: { kind: "fixed", ports: ["result"] },
};

const ACCEPTS: Record<NodeType, readonly PortType[]> = {
  input_logs: [],
  classifier: ["LogRecords", "Items"],
  run_python: ["LogRecords", "Items", "Classifications", "Value"],
  run_llm: ["LogRecords", "Items", "Classifications", "Value"],
  // Only aggregated results may be disclosed: never raw records or per-item text.
  final_report: ["Classifications", "Value"],
};

export function accepts(target: NodeType, type: PortType): boolean {
  return ACCEPTS[target].includes(type);
}

export function acceptedTypes(target: NodeType): readonly PortType[] {
  return ACCEPTS[target];
}

export function splitRef(r: string): [string, string] {
  const i = r.indexOf(".");
  return [r.slice(0, i), r.slice(i + 1)];
}

export function nodeById(plan: Plan): Map<string, PlanNode> {
  return new Map(plan.nodes.map((n) => [n.id, n]));
}

/** The type flowing into `node.inputs[name]`, or null if the reference does not resolve. */
export function inputType(plan: Plan, node: PlanNode, name: string): PortType | null {
  const r = node.inputs[name];
  if (!r) return null;
  const [src, port] = splitRef(r);
  const s = nodeById(plan).get(src);
  const out = s && outputPort(s);
  return out && out.name === port ? out.type : null;
}
