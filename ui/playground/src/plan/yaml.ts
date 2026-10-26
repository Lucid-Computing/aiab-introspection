/** The YAML projection of the IR, with errors located by line. */
import { Document, isMap, LineCounter, parseDocument } from "yaml";
import { type Issue, type Layout, LayoutSchema, type Plan } from "./types";
import { validatePlan } from "./validate";

const HEADER = " Audit plan (Plan IR). The graph view edits the same plan.";

/** Nodes without inputs omit the key (it defaults to empty). */
function compact(plan: Plan): object {
  return {
    ...plan,
    nodes: plan.nodes.map((n) => {
      if (Object.keys(n.inputs).length) return n;
      const { inputs: _, ...rest } = n;
      return rest;
    }),
  };
}

export function planToYaml(plan: Plan): string {
  const doc = new Document(compact(plan));
  doc.commentBefore = HEADER;
  return doc.toString({ lineWidth: 0, blockQuote: "literal" });
}

export type YamlResult =
  | { ok: true; plan: Plan; layout?: Layout }
  | { ok: false; issues: Issue[] };

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
    return { ok: false, issues: [{ severity: "error", line: 1, message: "The plan must be a YAML mapping (version, plan_id, nodes, …)" }] };
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
  const doc = new Document({ ...compact(plan), layout: positions });
  doc.commentBefore = `${HEADER}\n \`layout\` holds canvas positions only; it does not change what the plan does.`;
  return doc.toString({ lineWidth: 0, blockQuote: "literal" });
}
