/**
 * The Final Report is a sink with one input, `result`. Its fields are the shape it releases;
 * each field reads a path in that one input (`from`, which defaults to the field's name).
 * A Value input exposes its declared fields; Classifications expose only aggregates
 * (counts, proportions, "any", max score), never per-item results or evidence.
 */
import { nodeById, splitRef } from "./ports";
import type { FieldType, FinalReport, Plan, PlanNode, ReportField } from "./types";

export const REPORT_INPUT = "result";

export type Resolved = { ok: true; type: FieldType } | { ok: false; message: string };

/** Labels a Classifications output can carry, or null when not known statically. */
export function classifierLabels(source: PlanNode | undefined): string[] | null {
  if (source?.type !== "classifier") return null;
  return [...source.config.labels.map((l) => l.name), "abstain"];
}

/** The node whose output the report releases. */
export function reportSource(plan: Plan, report: FinalReport): PlanNode | undefined {
  const r = report.inputs[REPORT_INPUT];
  return r ? nodeById(plan).get(splitRef(r)[0]) : undefined;
}

/** Where a field's value comes from in the report's input. */
export function fieldPath(f: ReportField): string {
  return f.from ?? f.name;
}

export const CLASSIFICATION_PATHS = [
  { path: "count", type: "integer", note: "number of items classified" },
  { path: "max_score", type: "number", note: "highest score of any item" },
  { path: "count.<label>", type: "integer", note: "items with this label" },
  { path: "proportion.<label>", type: "number", note: "share of items with this label" },
  { path: "any.<label>", type: "boolean", note: "at least one item has this label" },
] as const;

export function resolveReportPath(plan: Plan, report: FinalReport, path: string): Resolved {
  const src = reportSource(plan, report);
  if (!src) return { ok: false, message: "the report has no input connected" };
  const out =
    src.type === "classifier"
      ? { type: "Classifications" as const, fields: {} as Record<string, FieldType> }
      : src.type === "run_python" || src.type === "run_llm"
        ? src.config.output
        : null;
  if (!out) return { ok: false, message: `"${src.id}" cannot be reported` };
  const parts = path.split(".");

  if (out.type === "Value") {
    const t = parts.length === 1 ? out.fields[parts[0]] : undefined;
    return t
      ? { ok: true, type: t }
      : {
          ok: false,
          message: `"${src.id}" declares no field "${path}" (fields: ${Object.keys(out.fields).join(", ") || "none"})`,
        };
  }
  if (out.type === "Classifications") {
    const labels = classifierLabels(src);
    const checkLabel = (l: string | undefined): string | null =>
      !l
        ? "missing label"
        : labels && !labels.includes(l)
          ? `unknown label "${l}" (labels: ${labels.join(", ")})`
          : null;
    const [head, label, ...extra] = parts;
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
      message: `classifications are reported only as aggregates: ${CLASSIFICATION_PATHS.map((p) => p.path).join(", ")}`,
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
