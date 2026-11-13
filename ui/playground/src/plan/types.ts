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
        /** Absent keeps every session of the source; otherwise only these sessions are read. */
        sessions: z.array(z.string().min(1).max(256)).optional(),
      })
      .default({ kinds: [...RECORD_KINDS], keywords: [] }),
    /** Retired: plans signed with a record limit keep it; the box ignores it. */
    max_records: z.number().int().positive().optional(),
  }),
});

/**
 * A Filter's rules: what each field of a record is tested with (static/aiab/baked/plan_ir.py
 * FIELD_OPS). Values are checked further by lint (`ruleIssue`), as the box does.
 */
export const FIELD_OPS = {
  content: ["contains", "not_contains", "equals", "starts_with", "ends_with", "regex"],
  kind: ["in"],
  agent: ["in", "not_in"],
  session: ["in", "not_in"],
  time: ["between", "before", "after"],
  length: ["at_least", "at_most"],
} as const;
export type RuleField = keyof typeof FIELD_OPS;
export const RULE_FIELDS = Object.keys(FIELD_OPS) as RuleField[];

const TextRule = z.strictObject({
  field: z.literal("content"),
  op: z.enum(FIELD_OPS.content),
  value: z.string(),
  case_sensitive: z.boolean().default(false),
});
const KindRule = z.strictObject({ field: z.literal("kind"), op: z.enum(FIELD_OPS.kind), value: z.array(z.string()) });
const AgentRule = z.strictObject({ field: z.literal("agent"), op: z.enum(FIELD_OPS.agent), value: z.array(z.string()) });
const SessionRule = z.strictObject({
  field: z.literal("session"),
  op: z.enum(FIELD_OPS.session),
  value: z.array(z.string()),
});
const TimeRule = z.strictObject({
  field: z.literal("time"),
  op: z.enum(FIELD_OPS.time),
  /** One UTC time for before / after, two for between. */
  value: z.union([z.string(), z.array(z.string())]),
});
const LengthRule = z.strictObject({
  field: z.literal("length"),
  op: z.enum(FIELD_OPS.length),
  value: z.number().int(),
});
export const Rule = z.discriminatedUnion("field", [TextRule, KindRule, AgentRule, SessionRule, TimeRule, LengthRule]);
export type Rule = z.infer<typeof Rule>;

/** A Filter carries only the settings of its mode, so a signed plan holds nothing that does not run. */
export const FilterRules = z.strictObject({
  mode: z.literal("rules"),
  match: z.enum(["all", "any"]).default("all"),
  /** Drop the records the rules match instead of keeping them. */
  exclude: z.boolean().default(false),
  rules: z.array(Rule).default([]),
});
export const FilterLlm = z.strictObject({
  mode: z.literal("llm"),
  llm: z.strictObject({
    model: Model,
    /** One yes/no question: yes keeps the record (or the session), no drops it. */
    question: z.string().default(""),
    judge: z.enum(["record", "session"]).default("record"),
    yes_examples: z.array(z.string()).default([]),
    no_examples: z.array(z.string()).default([]),
    /** Below this score the answer is unsure, and `unsure` decides. */
    threshold: z.number().min(0).max(1).optional(),
    unsure: z.enum(["drop", "keep"]).default("drop"),
  }),
});
export type FilterRules = z.infer<typeof FilterRules>;
export type FilterLlm = z.infer<typeof FilterLlm>;

export const FilterLogsNode = z.strictObject({
  ...node("filter_logs"),
  config: z.discriminatedUnion("mode", [FilterRules, FilterLlm]),
});

/** AND and OR: the records in both inputs, or in either, by record id, in log order. */
export const CombineLogsNode = z.strictObject({
  ...node("combine_logs"),
  config: z.strictObject({ op: z.enum(["and", "or"]) }),
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
  FilterLogsNode,
  CombineLogsNode,
  ClassifierNode,
  RunPythonNode,
  RunLlmNode,
  FinalReportNode,
]);

/**
 * Retired run limits. A run has no record, byte, model-call or time limit; plans signed when
 * it had them still carry them, and they load and are ignored (static/aiab/baked/plan_ir.py).
 * New plans have none.
 */
export const Limits = z.strictObject({
  max_input_utf8_bytes: z.number().int().min(1).optional(),
  max_model_calls_per_run: z.number().int().min(1).optional(),
  max_runtime_seconds_per_run: z.number().int().min(1).optional(),
});

export const PlanSchema = z.strictObject({
  version: z.literal(PLAN_VERSION),
  /** The agreement's name: the folder it is saved in. */
  plan_id: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/, "letters, digits, _ and -, not starting with _"),
  title: z.string().default(""),
  description: z.string().optional(),
  limits: Limits.optional(),
  nodes: z.array(PlanNode),
});

export type Plan = z.infer<typeof PlanSchema>;
export type PlanNode = z.infer<typeof PlanNode>;
export type NodeType = PlanNode["type"];
export type NodeOf<T extends NodeType> = Extract<PlanNode, { type: T }>;
export type InputLogs = NodeOf<"input_logs">;
export type FilterLogs = NodeOf<"filter_logs">;
export type CombineLogs = NodeOf<"combine_logs">;
export type Classifier = NodeOf<"classifier">;
export type RunPython = NodeOf<"run_python">;
export type RunLlm = NodeOf<"run_llm">;
export type FinalReport = NodeOf<"final_report">;

export const NODE_TYPES: NodeType[] = [
  "input_logs",
  "filter_logs",
  "combine_logs",
  "classifier",
  "run_python",
  "run_llm",
  "final_report",
];

/** Canvas positions by node id. Kept out of the IR: moving a box changes no semantics. */
export const LayoutSchema = z.record(z.string(), z.object({ x: z.number(), y: z.number() }));
export type Layout = z.infer<typeof LayoutSchema>;

export interface Issue {
  severity: "error" | "warning";
  /** An error every new block starts with (nothing connected, no fields yet): shown as unfinished, not broken. */
  kind?: "incomplete";
  message: string;
  nodeId?: string;
  /** Path into the plan document, for line numbers in the code view. */
  path?: (string | number)[];
  line?: number;
}
