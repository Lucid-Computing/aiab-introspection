/** The workbench v1 HTTP seam. Extra server fields are preserved on round-trip. */
export type Json =
  | null
  | boolean
  | number
  | string
  | Json[]
  | { [key: string]: Json };
export type ObjectData = Record<string, unknown>;
export const STEP_KINDS = [
  "select",
  "join",
  "aggregate",
  "rules",
  "sql",
  "python",
  "agent",
  "assert",
  "release",
] as const;
export type StepKind = (typeof STEP_KINDS)[number];
export interface Step extends ObjectData {
  id: string;
  kind: StepKind;
  title: string;
  inputs: string[];
  config: ObjectData;
}
export interface Recipient extends ObjectData {
  id: string;
  role: string;
  fields: string[];
  allow_evidence: boolean;
  allow_excerpts: boolean;
}
export interface Plan extends ObjectData {
  version: "aiab-workbench/v1";
  id: string;
  title: string;
  question: string;
  criteria: ({ id: string; description: string } & ObjectData)[];
  mode: "personal" | "joint" | "confidential";
  scope: ObjectData & {
    dataset_ids: string[];
    subjects: string[];
    time_start: string | null;
    time_end: string | null;
    fields: string[];
    required_sources: string[];
  };
  steps: Step[];
  agent: ObjectData & {
    provider: string;
    model: string;
    max_turns: number;
    max_tokens: number;
    max_tool_calls: number;
    max_seconds: number;
  };
  disclosure: ObjectData & {
    recipients: Recipient[];
    min_group_size: number;
    max_releases: number;
  };
  approval: ObjectData & {
    required_signers: string[];
    roster: string[];
    expires_at: string | null;
  };
}
export interface Dataset extends ObjectData {
  id: string;
  title: string;
  origin: string;
  manifest: ObjectData & {
    sources?: unknown[];
    coverage?: ObjectData;
    integrity?: string;
  };
}
export interface Audit extends ObjectData {
  id: string;
  title: string;
  question: string;
  requirements?: unknown;
  method?: string;
  validity?: string;
  validity_notes?: string[];
  runtime_validation?: { valid: boolean; diagnostics: string[] };
  plan?: Plan;
  plan_id?: string;
}
export interface SavedPlan {
  plan: Plan;
  digest: string;
  approvals?: unknown[];
}
export interface ExactChange { path: string; before: unknown; after: unknown }
export interface Suggestion {
  plan: Plan;
  changes: ExactChange[];
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number; test_model: boolean };
  verification: ObjectData;
}
export interface EvaluationCase {
  investigation_id: string;
  criterion_id: string;
  observed_events: number;
  status: string;
  outcome: string | null;
  failure?: string;
  usage?: ObjectData;
  coverage?: ObjectData;
}
export interface Evaluation {
  evaluation_id: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted";
  plan_digest: string;
  evaluated_plan: Plan;
  evaluated_digest: string;
  changes: ExactChange[];
  include_agent: boolean;
  case_limit: number;
  seed: number;
  evidence_mode: string;
  error?: string;
  metrics: Record<string, number | null> | null;
  report: {
    family_metrics?: Array<{ criterion_id: string; family: string; cases: number;
      accuracy: number; accuracy_denominator: number; precision: number | null;
      precision_denominator: number; recall: number | null; recall_denominator: number;
      unknown: number; abstentions: number }>;
    cases: EvaluationCase[];
    partial?: boolean;
    execution?: string;
    available_cases?: number;
    selected_cases?: number;
    excluded_by_limit?: number;
    limitations?: string[];
    test_model?: boolean;
  } | null;
}
export interface Bootstrap {
  user: unknown;
  capabilities: ObjectData;
  audits: Audit[];
  datasets: Dataset[];
  plans: (SavedPlan | Plan)[];
}
export interface Diagnostic extends ObjectData {
  message: string;
  severity?: string;
  path?: string;
  code?: string;
}
export interface Check {
  valid: boolean;
  diagnostics: (Diagnostic | string)[];
  requirements: unknown;
}
export interface Finding extends ObjectData {
  id: string;
  criterion_id: string;
  title: string;
  outcome: string;
  claim: string;
  evidence_ids: string[];
  counterevidence_ids: string[];
  limitations: string[];
  method: string;
  details: ObjectData;
}
export interface Run extends ObjectData {
  run_id?: string;
  id?: string;
  status: string;
  result?: ObjectData & {
    findings?: Finding[];
    status?: string;
    coverage?: unknown;
    trace?: unknown[];
    usage?: unknown;
  };
  findings?: Finding[];
  coverage?: unknown;
  usage?: unknown;
  error?: unknown;
}
