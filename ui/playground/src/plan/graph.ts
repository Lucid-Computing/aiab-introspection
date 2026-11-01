/**
 * The graph projection of the IR: React Flow nodes and edges. Each input binding is one
 * edge, from the source node's output handle to the target's input handle of the same name.
 * `flowToPlan` is the inverse; it rebuilds bindings from edges alone, so a round trip
 * through the graph proves the edges carry the connections.
 */
import type { Edge, Node } from "@xyflow/react";
import { splitRef } from "./ports";
import { describeNode, type NodeShape } from "./shape";
import type { Issue, Layout, Plan, PlanNode } from "./types";
import { type Result, validatePlan } from "./validate";

/** Target handle on dynamic-input nodes that creates a new named input when connected. */
export const NEW_INPUT = "__new__";

/** `io` is what the card shows for ports: inputs with the type each receives, the output and its shape. */
export type FlowNodeData = { node: PlanNode; issues: Issue[]; io: NodeShape };
export type FlowNode = Node<FlowNodeData, PlanNode["type"]>;
export type FlowEdgeData = { invalid: boolean };
export type FlowEdge = Edge<FlowEdgeData>;

export function planToFlow(
  plan: Plan,
  layout: Layout,
  issues: Issue[] = [],
  bad: Set<string> = new Set(),
): { nodes: FlowNode[]; edges: FlowEdge[] } {
  const nodes: FlowNode[] = plan.nodes.map((n) => ({
    id: n.id,
    type: n.type,
    position: layout[n.id] ?? { x: 0, y: 0 },
    data: { node: n, issues: issues.filter((i) => i.nodeId === n.id), io: describeNode(plan, n) },
  }));
  const edges: FlowEdge[] = plan.nodes.flatMap((n) =>
    Object.entries(n.inputs).map(([name, r]) => {
      const [source, port] = splitRef(r);
      const invalid = bad.has(`${n.id}.${name}`);
      return {
        id: edgeId(n.id, name),
        source,
        sourceHandle: port,
        target: n.id,
        targetHandle: name,
        label: port === name ? name : `${port} → ${name}`,
        data: { invalid },
      };
    }),
  );
  return { nodes, edges };
}

export function edgeId(target: string, input: string): string {
  return `${target}.${input}`;
}

/** Rebuild the plan from graph nodes and edges, then validate it like any other input. */
export function flowToPlan(
  meta: Pick<Plan, "version" | "plan_id" | "title" | "description">,
  nodes: FlowNode[],
  edges: FlowEdge[],
): Result {
  const raw = {
    ...meta,
    nodes: nodes.map((fn) => {
      const inputs: Record<string, string> = {};
      for (const e of edges)
        if (e.target === fn.id && e.targetHandle) inputs[e.targetHandle] = `${e.source}.${e.sourceHandle}`;
      return { ...fn.data.node, id: fn.id, inputs };
    }),
  };
  if (raw.description === undefined) delete raw.description;
  return validatePlan(raw);
}

export function layoutFromFlow(nodes: FlowNode[]): Layout {
  return Object.fromEntries(nodes.map((n) => [n.id, { x: n.position.x, y: n.position.y }]));
}
