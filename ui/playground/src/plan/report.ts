/**
 * What a Final Report field may read. A field names one input of the report and a path into
 * it. Value inputs expose their declared fields; Classifications expose only aggregates
 * (counts, proportions, "any", max score), never per-item results or evidence.
 */
import { nodeById, splitRef } from "./ports";
import type { FieldType, FinalReport, Plan, PlanNode } from "./types";

export type Resolved = { ok: true; type: FieldType } | { ok: false; message: string };

/** Labels a Classifications output can carry, or null when not known statically. */
export function classifierLabels(source: PlanNode | undefined): string[] | null {
  if (source?.type !== "classifier") return null;
  return [...source.config.labels.map((l) => l.name), "abstain"];
}

export function sourceOf(plan: Plan, report: FinalReport, input: string): PlanNode | undefined {
  const r = report.inputs[input];
  return r ? nodeById(plan).get(splitRef(r)[0]) : undefined;
}

export const CLASSIFICATION_PATHS = [
  { path: "count", type: "integer", note: "number of items classified" },
  { path: "max_score", type: "number", note: "highest score of any item" },
  { path: "count.<label>", type: "integer", note: "items with this label" },
  { path: "proportion.<label>", type: "number", note: "share of items with this label" },
  { path: "any.<label>", type: "boolean", note: "at least one item has this label" },
] as const;

export function resolveReportPath(plan: Plan, report: FinalReport, from: string): Resolved {
  const [input, ...rest] = from.split(".");
  if (!(input in report.inputs)) return { ok: false, message: `"${input}" is not an input of this report` };
  const src = sourceOf(plan, report, input);
  if (!src) return { ok: false, message: `input "${input}" is not connected` };
  const out =
    src.type === "classifier"
      ? { type: "Classifications" as const, fields: {} as Record<string, FieldType> }
      : src.type === "run_python" || src.type === "run_llm"
        ? src.config.output
        : null;
  if (!out) return { ok: false, message: `input "${input}" cannot be reported` };

  if (out.type === "Value") {
    if (rest.length !== 1)
      return {
        ok: false,
        message: `use "${input}.<field>" (fields: ${Object.keys(out.fields).join(", ") || "none declared"})`,
      };
    const t = out.fields[rest[0]];
    return t ? { ok: true, type: t } : { ok: false, message: `"${src.id}" declares no field "${rest[0]}"` };
  }
  if (out.type === "Classifications") {
    const labels = classifierLabels(src);
    const checkLabel = (l: string | undefined): string | null =>
      !l
        ? "missing label"
        : labels && !labels.includes(l)
          ? `unknown label "${l}" (labels: ${labels.join(", ")})`
          : null;
    const [head, label, ...extra] = rest;
    if (extra.length) return { ok: false, message: `path too long` };
    if (head === "count" && label === undefined) return { ok: true, type: "integer" };
    if (head === "max_score" && label === undefined) return { ok: true, type: "number" };
    if (head === "count" || head === "proportion" || head === "any") {
      const bad = checkLabel(label);
      if (bad) return { ok: false, message: bad };
      return { ok: true, type: head === "count" ? "integer" : head === "proportion" ? "number" : "boolean" };
    }
    return {
      ok: false,
      message: `classifications are reported only as aggregates: ${CLASSIFICATION_PATHS.map((p) => `${input}.${p.path}`).join(", ")}`,
    };
  }
  return { ok: false, message: `${out.type} cannot be reported` };
}

export function assignable(declared: FieldType, actual: FieldType): boolean {
  return declared === actual || (declared === "number" && actual === "integer");
}

/** The report's JSON Schema, in the same shape as plan.json's output_schema. */
export function reportJsonSchema(report: FinalReport): object {
  return {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    type: "object",
    properties: Object.fromEntries(
      report.config.fields.map((f) => [
        f.name,
        { type: f.type, ...(f.description ? { description: f.description } : {}) },
      ]),
    ),
    required: report.config.fields.map((f) => f.name),
    additionalProperties: false,
  };
}
