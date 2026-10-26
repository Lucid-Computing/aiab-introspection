/**
 * The Plan IR: the one canonical form of an audit plan. The graph and the YAML are both
 * projections of it. Connections live on the consuming node as input bindings
 * (`inputs: { name: "<node>.<port>" }`), so one output can feed many nodes and a node can
 * take several inputs. Canvas positions are not part of the IR (see `Layout`).
 */
import { z } from "zod";

export const PLAN_VERSION = "aiab-plan-ir/v0";

const ident = z.string().regex(/^[a-z][a-z0-9_]*$/, "use lower_snake_case: a letter, then letters, digits or _");

export const ref = z.string().regex(/^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$/, 'a reference is "<node_id>.<output>"');

/** What travels along an edge. Kept deliberately small. */
export const PORT_TYPES = ["LogRecords", "Items", "Classifications", "Value"] as const;
export const PortType = z.enum(PORT_TYPES);
export type PortType = z.infer<typeof PortType>;

/** Scalar types of a Value's fields and of report fields. */
export const FIELD_TYPES = ["boolean", "integer", "number", "string"] as const;
export const FieldType = z.enum(FIELD_TYPES);
export type FieldType = z.infer<typeof FieldType>;

export const RECORD_KINDS = ["request", "response", "tool_call", "tool_result"] as const;
export const RecordKind = z.enum(RECORD_KINDS);

const Model = z.strictObject({
  id: z.string().min(1),
  temperature: z.number().min(0).max(2).default(0),
  max_output_tokens: z.number().int().positive().default(512),
});

const Output = z.strictObject({
  name: ident,
  type: PortType,
  /** Only meaningful for type Value: the named fields it carries. */
  fields: z.record(ident, FieldType).default({}),
});
export type Output = z.infer<typeof Output>;

const Inputs = z.record(ident, ref).default({});

// Key order here is the key order in the YAML: id, type, title, inputs, config.
const node = <T extends string>(type: T) => ({
  id: ident,
  type: z.literal(type),
  title: z.string().optional(),
  inputs: Inputs,
});

export const InputLogsNode = z.strictObject({
  ...node("input_logs"),
  config: z.strictObject({
    source: z.string().min(1),
    input_scope: z
      .strictObject({
        window_start: z.string().optional(),
        window_end: z.string().optional(),
        /** Empty means every agent in the source. */
        agents: z.array(z.string()).default([]),
      })
      .default({ agents: [] }),
    filter: z
      .strictObject({
        kinds: z.array(RecordKind).default([...RECORD_KINDS]),
        /** Empty keeps every record; otherwise a record must contain one keyword. */
        keywords: z.array(z.string()).default([]),
      })
      .default({ kinds: [...RECORD_KINDS], keywords: [] }),
    max_records: z.number().int().positive().default(2000),
  }),
});

export const ClassifierNode = z.strictObject({
  ...node("classifier"),
  config: z.strictObject({
    model: Model,
    /** With LogRecords input: classify each session, or each record on its own. */
    group_by: z.enum(["session", "record"]).default("session"),
    /** Most records the model reads in one call (the box's window). */
    window: z.number().int().positive().default(8),
    instructions: z.string().default(""),
    labels: z
      .array(
        z.strictObject({
          name: ident,
          description: z.string().default(""),
          /** Counts as a finding in reports (e.g. `any.<label>`). */
          flag: z.boolean().default(false),
        }),
      )
      .default([]),
    /** Below this score the item gets the label `abstain`. */
    threshold: z.number().min(0).max(1).optional(),
    positive_examples: z.array(z.string()).default([]),
    negative_examples: z.array(z.string()).default([]),
    output_contract: z
      .strictObject({
        score: z.boolean().default(true),
        evidence_ids: z.boolean().default(true),
      })
      .default({ score: true, evidence_ids: true }),
  }),
});

export const RunPythonNode = z.strictObject({
  ...node("run_python"),
  config: z.strictObject({
    code: z.string().default(""),
    output: Output,
  }),
});

export const RunLlmNode = z.strictObject({
  ...node("run_llm"),
  config: z.strictObject({
    model: Model,
    system_prompt: z.string().default(""),
    /** `{{name}}` refers to a declared input. */
    user_prompt: z.string().default(""),
    /** Items: one call per item of the single list input. Value: one call in total. */
    output: Output,
  }),
});

export const ReportField = z.strictObject({
  name: ident,
  type: FieldType,
  /** Path in the report's input, e.g. "flagged" or "proportion.physics". Defaults to `name`. */
  from: z.string().min(1).optional(),
  description: z.string().optional(),
});
export type ReportField = z.infer<typeof ReportField>;

export const FinalReportNode = z.strictObject({
  ...node("final_report"),
  config: z.strictObject({
    fields: z.array(ReportField).default([]),
    privacy: z
      .strictObject({
        /** String fields may carry model-written text; off by default. */
        allow_free_text: z.boolean().default(false),
        /** Counts below this are released as 0 (suppressed). 1 releases every count. */
        min_count: z.number().int().min(1).default(1),
        max_result_bytes: z.number().int().positive().default(1024),
      })
      .default({ allow_free_text: false, min_count: 1, max_result_bytes: 1024 }),
  }),
});

export const PlanNode = z.discriminatedUnion("type", [
  InputLogsNode,
  ClassifierNode,
  RunPythonNode,
  RunLlmNode,
  FinalReportNode,
]);

export const PlanSchema = z.strictObject({
  version: z.literal(PLAN_VERSION),
  plan_id: ident,
  title: z.string().default(""),
  description: z.string().optional(),
  nodes: z.array(PlanNode),
});

export type Plan = z.infer<typeof PlanSchema>;
export type PlanNode = z.infer<typeof PlanNode>;
export type NodeType = PlanNode["type"];
export type NodeOf<T extends NodeType> = Extract<PlanNode, { type: T }>;
export type InputLogs = NodeOf<"input_logs">;
export type Classifier = NodeOf<"classifier">;
export type RunPython = NodeOf<"run_python">;
export type RunLlm = NodeOf<"run_llm">;
export type FinalReport = NodeOf<"final_report">;

export const NODE_TYPES: NodeType[] = ["input_logs", "classifier", "run_python", "run_llm", "final_report"];

/** Canvas positions by node id. Kept out of the IR: moving a box changes no semantics. */
export const LayoutSchema = z.record(z.string(), z.object({ x: z.number(), y: z.number() }));
export type Layout = z.infer<typeof LayoutSchema>;

export interface Issue {
  severity: "error" | "warning";
  message: string;
  nodeId?: string;
  /** Path into the plan document, for line numbers in the code view. */
  path?: (string | number)[];
  line?: number;
}
