/** Automatic placement for plans that arrive without positions (code edits, imports). */
import { Graph, layout as dagreLayout } from "@dagrejs/dagre";
import { splitRef } from "./ports";
import type { Layout, Plan, PlanNode } from "./types";

export const NODE_W = 220;
export const NODE_H = 80;

/** Rough rendered height: nodes with several named inputs list them. */
export function nodeHeight(n: PlanNode): number {
  const inputs = Object.keys(n.inputs).length;
  return NODE_H + (n.type !== "classifier" && inputs > 1 ? 10 + 17 * inputs : 0);
}

/** Left-to-right layered layout of the whole plan. */
export function autoLayout(plan: Plan): Layout {
  const g = new Graph();
  g.setGraph({ rankdir: "LR", nodesep: 40, ranksep: 70, marginx: 20, marginy: 20 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of plan.nodes) g.setNode(n.id, { width: NODE_W, height: nodeHeight(n) });
  for (const n of plan.nodes)
    for (const r of Object.values(n.inputs)) {
      const src = splitRef(r)[0];
      if (g.hasNode(src)) g.setEdge(src, n.id);
    }
  dagreLayout(g);
  return Object.fromEntries(
    plan.nodes.map((n) => {
      const p = g.node(n.id);
      return [n.id, { x: p.x - NODE_W / 2, y: p.y - nodeHeight(n) / 2 }];
    }),
  );
}

/**
 * Keep known positions; place nodes that have none. A new node goes where the automatic
 * layout would put it, shifted so it does not land on an existing box.
 */
export function completeLayout(plan: Plan, layout: Layout): Layout {
  const missing = plan.nodes.filter((n) => !layout[n.id]);
  const kept: Layout = {};
  for (const n of plan.nodes) if (layout[n.id]) kept[n.id] = layout[n.id];
  if (!missing.length) return kept;
  if (missing.length === plan.nodes.length) return autoLayout(plan);
  const auto = autoLayout(plan);
  const placed = Object.values(kept);
  for (const n of missing) {
    const p = { ...auto[n.id] };
    while (placed.some((q) => Math.abs(q.x - p.x) < NODE_W && Math.abs(q.y - p.y) < NODE_H)) p.y += NODE_H + 30;
    kept[n.id] = p;
    placed.push(p);
  }
  return kept;
}
