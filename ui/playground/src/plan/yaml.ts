/** The YAML projection of the IR, with errors located by line. */
import { Document, isMap, isScalar, isSeq, LineCounter, parseDocument, visit } from "yaml";
import type { z } from "zod";
import { type Issue, type Layout, LayoutSchema, type Plan, PlanNode } from "./types";
import { validatePlan } from "./validate";

const HEADER = " Audit plan. Edits here update the graph, and graph edits update this.";

/**
 * The plan without values that equal their defaults, so the code shows only what the plan
 * sets. Parsing fills the defaults back in, so this changes no semantics.
 */
export function minimal(plan: Plan): object {
  const same = (schema: z.ZodType, candidate: unknown, target: string) => {
    const r = schema.safeParse(candidate);
    return r.success && JSON.stringify(r.data) === target;
  };
  // Drop keys depth-first; keep a removal only if the parsed result is unchanged.
  const prune = (
    schema: z.ZodType,
    root: unknown,
    obj: Record<string, unknown>,
    target: string,
    keep: string[] = [],
  ) => {
    for (const key of Object.keys(obj)) {
      if (keep.includes(key)) continue;
      const value = obj[key];
      delete obj[key];
      if (same(schema, root, target)) continue;
      obj[key] = value;
      const children = Array.isArray(value) ? value : [value];
      for (const child of children)
        if (child && typeof child === "object" && !Array.isArray(child))
          prune(schema, root, child as Record<string, unknown>, target);
    }
  };
  const nodes = plan.nodes.map((n) => {
    const copy = structuredClone(n) as Record<string, unknown>;
    prune(PlanNode, copy, copy, JSON.stringify(PlanNode.parse(n)), ["id", "type"]);
    return copy;
  });
  const { nodes: _, ...meta } = plan;
  const top = structuredClone(meta) as Record<string, unknown>;
  if (top.title === "") delete top.title;
  return { ...top, nodes };
}

/** Short maps on one line: `{ name: flagged, type: boolean, from: v.flagged }`. */
const FLOW_KEYS = new Set(["model", "fields", "output_contract", "privacy", "input_scope", "filter"]);

function toDocument(value: object): Document {
  const doc = new Document(value);
  visit(doc, {
    Pair(_, pair) {
      const key = isScalar(pair.key) ? String(pair.key.value) : "";
      if (!FLOW_KEYS.has(key)) return;
      const v = pair.value;
      if (isMap(v) && v.items.every((p) => isScalar(p.value))) v.flow = true;
      if (isSeq(v)) for (const item of v.items) if (isMap(item) && item.items.length <= 4) item.flow = true;
    },
  });
  return doc;
}

export function planToYaml(plan: Plan): string {
  const doc = toDocument(minimal(plan));
  doc.commentBefore = HEADER;
  return doc.toString({ lineWidth: 0, blockQuote: "literal" });
}

export type YamlResult = { ok: true; plan: Plan; layout?: Layout } | { ok: false; issues: Issue[] };

/**
 * Parse YAML into a valid plan. With `allowLayout`, a top-level `layout:` map (as written by
 * export) is split off and returned separately; the code view does not accept one.
 */
export function yamlToPlan(text: string, { allowLayout = false } = {}): YamlResult {
  const lines = new LineCounter();
  const doc = parseDocument(text, { lineCounter: lines, prettyErrors: false });
  if (doc.errors.length)
    return {
      ok: false,
      issues: doc.errors.map((e) => ({
        severity: "error",
        line: lines.linePos(e.pos[0]).line,
        message: `YAML syntax: ${e.message.split("\n")[0]}`,
      })),
    };
  let raw: unknown = doc.toJS();
  let layout: Layout | undefined;
  if (allowLayout && raw && typeof raw === "object" && "layout" in raw) {
    const { layout: l, ...rest } = raw as Record<string, unknown>;
    const parsed = LayoutSchema.safeParse(l);
    if (parsed.success) layout = parsed.data;
    raw = rest;
  }
  if (raw === null || typeof raw !== "object" || Array.isArray(raw))
    return {
      ok: false,
      issues: [{ severity: "error", line: 1, message: "The plan must be a YAML mapping (version, plan_id, nodes, …)" }],
    };
  const result = validatePlan(raw);
  if (result.ok) return { ok: true, plan: result.plan, layout };
  return {
    ok: false,
    issues: result.issues.map((i) => ({ ...i, line: i.path ? lineOf(doc, lines, i.path) : undefined })),
  };
}

/** Line of the deepest existing YAML node on `path` (1-based). */
export function lineOf(doc: Document, lines: LineCounter, path: (string | number)[]): number | undefined {
  for (let k = path.length; k >= 0; k--) {
    const node = k === 0 ? doc.contents : doc.getIn(path.slice(0, k), true);
    const range = (node as { range?: [number, number, number] } | undefined)?.range;
    if (range) return lines.linePos(range[0]).line;
    // A missing key inside an existing map: point at the map.
    if (k > 0 && isMap(doc.getIn(path.slice(0, k - 1), true))) continue;
  }
  return undefined;
}

/** Locate issues found on an already-valid plan (lint) in the current YAML text. */
export function locateIssues(text: string, issues: Issue[]): Issue[] {
  const lines = new LineCounter();
  const doc = parseDocument(text, { lineCounter: lines });
  return issues.map((i) => (i.path && i.line === undefined ? { ...i, line: lineOf(doc, lines, i.path) } : i));
}

/** The export file: the plan plus a separate `layout` section the semantics ignore. */
export function exportYaml(plan: Plan, layout: Layout): string {
  const positions = Object.fromEntries(
    plan.nodes
      .filter((n) => layout[n.id])
      .map((n) => [n.id, { x: Math.round(layout[n.id].x), y: Math.round(layout[n.id].y) }]),
  );
  const doc = toDocument({ ...minimal(plan), layout: positions });
  doc.commentBefore = `${HEADER}\n \`layout\` holds canvas positions only; it does not change what the plan does.`;
  return doc.toString({ lineWidth: 0, blockQuote: "literal" });
}
