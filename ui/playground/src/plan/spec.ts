/**
 * The output specification of a block: what one value it hands on looks like, field by
 * field. This is what the next block may rely on and what the box checks before passing
 * the value on. For Code and LLM blocks the fields are declared by the plan; for the
 * fixed blocks they follow from the block kind and its settings.
 */
import type { PlanNode } from "./types";

export interface OutputSpec {
  /** "a list of records", "one object", "the report"… */
  kind: string;
  fields: { name: string; type: string }[];
}

const RECORD = [
  { name: "id", type: "string" },
  { name: "agent_id", type: "string" },
  { name: "session_id", type: "string" },
  { name: "ts", type: "string, a time" },
  { name: "kind", type: "request | response | tool_call | tool_result" },
  { name: "content", type: "string" },
];
const ITEM = [
  { name: "id", type: "string" },
  { name: "text", type: "string" },
  { name: "record_ids", type: "list of string" },
];

function ofType(type: string, fields: Record<string, string>, labels?: string[]): OutputSpec {
  switch (type) {
    case "LogRecords":
      return { kind: "a list of records", fields: RECORD };
    case "Items":
      return { kind: "a list of items, one per question", fields: ITEM };
    case "Classifications":
      return {
        kind: "a list of answers, one per item",
        fields: [
          { name: "item_id", type: "string" },
          { name: "label", type: labels?.length ? [...labels, "abstain"].join(" | ") : "string" },
          { name: "score", type: "number, 0 to 1" },
          { name: "evidence_ids", type: "list of string" },
        ],
      };
    default:
      return { kind: "one object", fields: Object.entries(fields).map(([name, t]) => ({ name, type: t })) };
  }
}

export function outputSpec(node: PlanNode): OutputSpec {
  switch (node.type) {
    case "input_logs":
    case "filter_logs":
    case "combine_logs":
      return ofType("LogRecords", {});
    case "classifier":
      return ofType(
        "Classifications",
        {},
        node.config.labels.map((l) => l.name),
      );
    case "run_python":
    case "run_llm":
      return ofType(node.config.output.type, node.config.output.fields);
    case "final_report":
      return {
        kind: "the report, the only thing that leaves the box",
        fields: node.config.fields.map((f) => ({ name: f.name, type: f.type })),
      };
    case "records_report": {
      const c = node.config;
      const types: Record<string, string> = {
        agent_ids: "list of string",
        kind_counts: "{request, response, tool_call, tool_result: integer}",
        first_ts: "string, a time",
        last_ts: "string, a time",
      };
      const fields: OutputSpec["fields"] = c.metadata.map((name) => ({ name, type: types[name] ?? "integer" }));
      if (c.records)
        fields.push(
          { name: "records_total", type: "integer" },
          { name: "records_included", type: "integer" },
          {
            name: "records",
            type: `list of {${[...c.records.fields, ...(c.records.fields.includes("content") ? ["content_length"] : [])].join(", ")}}`,
          },
        );
      return { kind: "the report, the only thing that leaves the box", fields };
    }
  }
}
