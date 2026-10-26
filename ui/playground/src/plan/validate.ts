/**
 * Validation in two tiers.
 *
 * `validatePlan` is the gate into the IR: the schema, unique ids, references that resolve
 * and an acyclic graph. Anything failing it is not a plan, so the code view keeps showing
 * the last valid graph. Every IR value in the app has passed it.
 *
 * `lintPlan` (lint.ts) finds problems in a well-formed plan — wrong port types, missing
 * inputs, report fields that do not resolve. Those are shown on the graph, not blocked,
 * so a plan can be edited step by step.
 */
import type { z } from "zod";
import { INPUT_RULES, outputPort, splitRef } from "./ports";
import { type Issue, type Plan, PlanSchema } from "./types";

export type Result = { ok: true; plan: Plan } | { ok: false; issues: Issue[] };

export function validatePlan(raw: unknown): Result {
  const parsed = PlanSchema.safeParse(raw);
  if (!parsed.success) return { ok: false, issues: zodIssues(parsed.error, raw) };
  const issues = structuralIssues(parsed.data);
  return issues.length ? { ok: false, issues } : { ok: true, plan: parsed.data };
}

function zodIssues(error: z.ZodError, raw: unknown): Issue[] {
  return error.issues.map((i) => {
    const path = i.path.map((p) => (typeof p === "symbol" ? String(p) : p));
    return {
      severity: "error" as const,
      path,
      nodeId: nodeIdAt(raw, path),
      message: `${describePath(raw, path)}: ${i.message}`,
    };
  });
}

function nodeIdAt(raw: unknown, path: (string | number)[]): string | undefined {
  if (path[0] !== "nodes" || typeof path[1] !== "number") return undefined;
  const nodes = (raw as { nodes?: unknown })?.nodes;
  const n = Array.isArray(nodes) ? nodes[path[1]] : undefined;
  return typeof n?.id === "string" ? n.id : undefined;
}

/** "nodes[2] (classify).config.threshold" — the node id makes paths readable. */
export function describePath(raw: unknown, path: (string | number)[]): string {
  if (!path.length) return "plan";
  const id = nodeIdAt(raw, path);
  let out = "";
  path.forEach((p, k) => {
    if (typeof p === "number") out += `[${p}]`;
    else out += (out ? "." : "") + p;
    if (k === 1 && path[0] === "nodes" && id) out += ` (${id})`;
  });
  return out;
}

export function structuralIssues(plan: Plan): Issue[] {
  const issues: Issue[] = [];
  const seen = new Map<string, number>();
  plan.nodes.forEach((n, i) => {
    if (seen.has(n.id))
      issues.push({
        severity: "error",
        nodeId: n.id,
        path: ["nodes", i, "id"],
        message: `Duplicate node id "${n.id}" (also nodes[${seen.get(n.id)}])`,
      });
    else seen.set(n.id, i);
  });
  const byId = new Map(plan.nodes.map((n) => [n.id, n]));

  plan.nodes.forEach((n, i) => {
    const rule = INPUT_RULES[n.type];
    for (const [name, r] of Object.entries(n.inputs)) {
      const path = ["nodes", i, "inputs", name];
      if (rule.kind === "none") {
        issues.push({ severity: "error", nodeId: n.id, path, message: `${n.id}: Input Logs takes no inputs` });
        continue;
      }
      if (rule.kind === "fixed" && !rule.ports.includes(name)) {
        issues.push({
          severity: "error",
          nodeId: n.id,
          path,
          message: `${n.id}: unknown input "${name}" (allowed: ${rule.ports.join(", ")})`,
        });
        continue;
      }
      const [src, port] = splitRef(r);
      const s = byId.get(src);
      if (!s) {
        issues.push({ severity: "error", nodeId: n.id, path, message: `${n.id}.inputs.${name}: no node "${src}"` });
        continue;
      }
      if (src === n.id) {
        issues.push({ severity: "error", nodeId: n.id, path, message: `${n.id}: a node cannot read its own output` });
        continue;
      }
      const out = outputPort(s);
      if (!out || out.name !== port)
        issues.push({
          severity: "error",
          nodeId: n.id,
          path,
          message: out
            ? `${n.id}.inputs.${name}: "${src}" has no output "${port}" (its output is "${out.name}")`
            : `${n.id}.inputs.${name}: "${src}" is a Final Report and has no output`,
        });
    }
  });
  if (issues.length) return issues;

  const cycle = findCycle(plan);
  if (cycle)
    issues.push({
      severity: "error",
      nodeId: cycle[0],
      path: ["nodes", plan.nodes.findIndex((n) => n.id === cycle[0]), "inputs"],
      message: `Cycle: ${cycle.join(" → ")}. A plan must be a directed acyclic graph.`,
    });
  return issues;
}

/** Upstream node ids of each node (deduplicated, in binding order). */
export function upstream(plan: Plan): Map<string, string[]> {
  return new Map(
    plan.nodes.map((n) => [n.id, [...new Set(Object.values(n.inputs).map((r) => splitRef(r)[0]))]]),
  );
}

/** A cycle as a list of ids in data-flow order (first repeated at the end), or null. */
export function findCycle(plan: Plan): string[] | null {
  const ups = upstream(plan);
  const state = new Map<string, 1 | 2>(); // 1 on stack, 2 done
  const stack: string[] = [];
  const visit = (id: string): string[] | null => {
    state.set(id, 1);
    stack.push(id);
    for (const u of ups.get(id) ?? []) {
      if (!ups.has(u)) continue;
      if (state.get(u) === 1) {
        const loop = stack.slice(stack.indexOf(u));
        return [...loop, u].reverse();
      }
      if (!state.has(u)) {
        const c = visit(u);
        if (c) return c;
      }
    }
    stack.pop();
    state.set(id, 2);
    return null;
  };
  for (const n of plan.nodes) {
    if (!state.has(n.id)) {
      const c = visit(n.id);
      if (c) return c;
    }
  }
  return null;
}

/** Would binding `source` into `target` close a cycle? (target already reaches source) */
export function wouldCycle(plan: Plan, source: string, target: string): boolean {
  if (source === target) return true;
  const ups = upstream(plan);
  const todo = [source];
  const seen = new Set<string>();
  while (todo.length) {
    const id = todo.pop()!;
    if (id === target) return true;
    if (seen.has(id)) continue;
    seen.add(id);
    todo.push(...(ups.get(id) ?? []));
  }
  return false;
}

/** Node ids in an order where every node comes after its inputs. Assumes no cycle. */
export function topoOrder(plan: Plan): string[] {
  const ups = upstream(plan);
  const out: string[] = [];
  const done = new Set<string>();
  const visit = (id: string) => {
    if (done.has(id)) return;
    done.add(id);
    for (const u of ups.get(id) ?? []) if (ups.has(u)) visit(u);
    out.push(id);
  };
  plan.nodes.forEach((n) => visit(n.id));
  return out;
}
