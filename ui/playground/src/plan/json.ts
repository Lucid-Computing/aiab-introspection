/**
 * The JSON projection of the plan: the text in the builder's code panel is plan.json as the
 * box saves it (every key, defaults written out, keys sorted), with line breaks and
 * indentation added. Errors are located by line.
 */
import { type Document, isMap, LineCounter, parseDocument } from "yaml";
import { type Issue, type Layout, LayoutSchema, type Plan } from "./types";
import { validatePlan } from "./validate";

/** Keys sorted at every level, the order the saved file has, so the text matches what is signed. */
function sorted(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sorted);
  if (value && typeof value === "object") {
    const o = value as Record<string, unknown>;
    return Object.fromEntries(
      Object.keys(o)
        .sort()
        .map((k) => [k, sorted(o[k])]),
    );
  }
  return value;
}

export function planToJson(plan: Plan): string {
  return `${JSON.stringify(sorted(plan), null, 2)}\n`;
}

export type JsonResult = { ok: true; plan: Plan; layout?: Layout } | { ok: false; issues: Issue[] };

/**
 * Where JSON syntax breaks: the offset of the first character that cannot continue a valid
 * document. Engines word their SyntaxError differently and most give no position, so this
 * small scanner finds it; it only runs once JSON.parse has already refused the text.
 */
export function syntaxErrorOffset(text: string): number {
  let i = 0;
  const n = text.length;
  const ws = () => {
    while (i < n && " \t\n\r".includes(text[i])) i++;
  };
  const fail = (at: number): never => {
    throw at;
  };
  const string = () => {
    if (text[i] !== '"') fail(i);
    i++;
    while (i < n && text[i] !== '"') {
      if (text[i] === "\\") {
        i++;
        if (i >= n) fail(i);
        if (text[i] === "u") {
          if (!/^[0-9a-fA-F]{4}$/.test(text.slice(i + 1, i + 5))) fail(i + 1);
          i += 4;
        } else if (!'"\\/bfnrt'.includes(text[i])) fail(i);
      } else if (text.charCodeAt(i) < 0x20) fail(i);
      i++;
    }
    if (i >= n) fail(n);
    i++;
  };
  const value = (): void => {
    ws();
    if (i >= n) fail(n);
    const c = text[i];
    if (c === "{") {
      i++;
      ws();
      if (text[i] === "}") return void i++;
      for (;;) {
        ws();
        string();
        ws();
        if (text[i] !== ":") fail(i);
        i++;
        value();
        ws();
        if (text[i] === ",") {
          i++;
          continue;
        }
        if (text[i] === "}") return void i++;
        fail(i);
      }
    }
    if (c === "[") {
      i++;
      ws();
      if (text[i] === "]") return void i++;
      for (;;) {
        value();
        ws();
        if (text[i] === ",") {
          i++;
          continue;
        }
        if (text[i] === "]") return void i++;
        fail(i);
      }
    }
    if (c === '"') return string();
    const m = /^(?:-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null)/.exec(text.slice(i, i + 64));
    if (!m) fail(i);
    i += m![0].length;
  };
  try {
    value();
    ws();
    if (i < n) fail(i);
    return n;
  } catch (at) {
    return typeof at === "number" ? at : 0;
  }
}

/** The line (1-based) of the first JSON syntax error. */
function syntaxLine(text: string): number {
  return text.slice(0, syntaxErrorOffset(text)).split("\n").length;
}

/**
 * Parse JSON into a valid plan. With `allowLayout`, a top-level `layout` object (as written
 * by export) is split off and returned separately; the code panel does not accept one.
 */
export function jsonToPlan(text: string, { allowLayout = false } = {}): JsonResult {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (e) {
    const err = e as SyntaxError;
    return {
      ok: false,
      issues: [{ severity: "error", line: syntaxLine(text), message: `JSON syntax: ${err.message}` }],
    };
  }
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
      issues: [{ severity: "error", line: 1, message: "The plan must be a JSON object (version, plan_id, nodes, …)" }],
    };
  const result = validatePlan(raw);
  if (result.ok) return { ok: true, plan: result.plan, layout };
  return { ok: false, issues: locateIssues(text, result.issues) };
}

/**
 * Positions in the text. JSON is valid YAML, and the YAML parser records where every value
 * starts and ends; that is all it is used for here.
 */
function positions(text: string) {
  const lines = new LineCounter();
  const doc = parseDocument(text, { lineCounter: lines, prettyErrors: false });
  return { doc, lines };
}

/** Line of the deepest existing value on `path` (1-based). */
function lineOf(doc: Document, lines: LineCounter, path: (string | number)[]): number | undefined {
  for (let k = path.length; k >= 0; k--) {
    const node = k === 0 ? doc.contents : doc.getIn(path.slice(0, k), true);
    const range = (node as { range?: [number, number, number] } | undefined)?.range;
    if (range) return lines.linePos(range[0]).line;
    // A missing key inside an existing object: point at the object.
    if (k > 0 && isMap(doc.getIn(path.slice(0, k - 1), true))) continue;
  }
  return undefined;
}

/** Locate issues that carry a path (schema, lint) in the current text. */
export function locateIssues(text: string, issues: Issue[]): Issue[] {
  const { doc, lines } = positions(text);
  if (doc.errors.length) return issues;
  return issues.map((i) => (i.path && i.line === undefined ? { ...i, line: lineOf(doc, lines, i.path) } : i));
}

/** First and last line of the value at `path`, to highlight a block in the editor. */
export function linesOf(text: string, path: (string | number)[]): { from: number; to: number } | undefined {
  const { doc, lines } = positions(text);
  if (doc.errors.length) return undefined;
  const node = doc.getIn(path, true) as { range?: [number, number, number] } | undefined;
  if (!node?.range) return undefined;
  return { from: lines.linePos(node.range[0]).line, to: lines.linePos(Math.max(node.range[0], node.range[1] - 1)).line };
}

/** The export file: the plan plus a separate `layout` object the semantics ignore. */
export function exportJson(plan: Plan, layout: Layout): string {
  const positionsOf = Object.fromEntries(
    plan.nodes
      .filter((n) => layout[n.id])
      .map((n) => [n.id, { x: Math.round(layout[n.id].x), y: Math.round(layout[n.id].y) }]),
  );
  return `${JSON.stringify(sorted({ ...plan, layout: positionsOf }), null, 2)}\n`;
}
