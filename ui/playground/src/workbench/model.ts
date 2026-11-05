import {
  STEP_KINDS,
  type Plan,
  type Step,
  type Run,
  type ObjectData,
  type Check,
  type SavedPlan,
} from "./types";

export const pretty = (value: unknown): string =>
  value === undefined ? "Not supplied" : JSON.stringify(value, null, 2);
export const csv = (value: string): string[] =>
  value
    .split(/[\n,]/)
    .map((x) => x.trim())
    .filter(Boolean);
export function blankPlan(): Plan {
  return {
    version: "aiab-workbench/v1",
    id: `audit-${Date.now().toString(36)}`,
    title: "Untitled investigation",
    question: "",
    criteria: [{ id: "criterion-1", description: "" }],
    mode: "personal",
    scope: {
      dataset_ids: [],
      subjects: [],
      time_start: null,
      time_end: null,
      fields: [],
      required_sources: [],
    },
    steps: [],
    agent: {
      provider: "tinfoil",
      model: "deepseek-v4-1-flash",
      max_turns: 12,
      max_tokens: 32000,
      max_tool_calls: 40,
      max_seconds: 180,
    },
    disclosure: {
      recipients: [
        {
          id: "owner",
          role: "owner",
          fields: ["findings", "coverage"],
          allow_evidence: true,
          allow_excerpts: true,
        },
      ],
      min_group_size: 1,
      max_releases: 1,
    },
    approval: { required_signers: [], roster: [], expires_at: null },
  };
}
const verbs: Record<string, string> = {
  select: "Find",
  join: "Link",
  aggregate: "Measure",
  rules: "Compare",
  sql: "Query",
  python: "Compute",
  agent: "Investigate",
  assert: "Check",
  release: "Release",
};
export function describeStep(step: Step): string {
  const inputs = step.inputs.length
    ? step.inputs.join(", ")
    : "the authorized dataset";
  if (step.kind === "python" || step.kind === "sql")
    return `${verbs[step.kind]} with custom ${step.kind} over ${inputs}. Review the code and input/output contract; its full meaning is not inferred.`;
  if (step.kind === "agent")
    return `Investigate ${inputs} adaptively under the signed model, tool and budget limits. Settings below define the mandate.`;
  return `${verbs[step.kind] ?? step.kind} ${inputs}. The executable settings below are authoritative.`;
}
/** Advisory structural checks only; the backend owns admission and operator semantics. */
export function localIssues(plan: Plan): string[] {
  const issues: string[] = [];
  if (!plan.id.trim() || !plan.title.trim() || !plan.question.trim())
    issues.push("Supply a plan ID, title and investigation question.");
  if (
    !plan.criteria.length ||
    plan.criteria.some((c) => !c.id || !c.description.trim())
  )
    issues.push("Every criterion needs an ID and a decision rule.");
  if (!plan.scope.dataset_ids.length)
    issues.push(
      "Choose an evidence dataset. Unspecified scope grants no access.",
    );
  if (!plan.steps.length) issues.push("Add an investigation step.");
  const seen = new Set<string>();
  for (const step of plan.steps) {
    if (!step.id || seen.has(step.id))
      issues.push(`Step ID '${step.id}' is empty or duplicated.`);
    if (!STEP_KINDS.includes(step.kind))
      issues.push(`Unsupported operator: ${step.kind}.`);
    for (const input of step.inputs)
      if (!seen.has(input))
        issues.push(`${step.id}: input '${input}' must name a preceding step.`);
    seen.add(step.id);
  }
  if (
    plan.agent.provider !== "tinfoil" ||
    plan.agent.model !== "deepseek-v4-1-flash"
  )
    issues.push(
      "This workbench requires the approved DeepSeek/Tinfoil profile. Backend compatibility must be checked.",
    );
  for (const [key, value] of Object.entries(plan.agent))
    if (
      key.startsWith("max_") &&
      (typeof value !== "number" || !Number.isFinite(value) || value <= 0)
    )
      issues.push(`${key} must be a positive number.`);
  if (
    plan.scope.time_start &&
    plan.scope.time_end &&
    plan.scope.time_start > plan.scope.time_end
  )
    issues.push("The evidence period ends before it starts.");
  if (!plan.disclosure.recipients.length)
    issues.push("Declare at least one recipient.");
  return issues;
}
/** Parse a draft without stripping fields owned by newer backend versions. */
export function parsePlan(text: string): Plan {
  const p = JSON.parse(text);
  if (
    !p ||
    p.version !== "aiab-workbench/v1" ||
    typeof p.id !== "string" ||
    typeof p.title !== "string" ||
    typeof p.question !== "string" ||
    !["personal", "joint", "confidential"].includes(p.mode)
  )
    throw new Error(
      "Expected an aiab-workbench/v1 plan with ID, title, question and mode.",
    );
  if (
    !Array.isArray(p.criteria) ||
    p.criteria.some(
      (c: ObjectData) =>
        !c || typeof c.id !== "string" || typeof c.description !== "string",
    )
  )
    throw new Error("Criteria must have IDs and descriptions.");
  if (
    !p.scope ||
    ["dataset_ids", "subjects", "fields", "required_sources"].some(
      (k) =>
        !Array.isArray(p.scope[k]) ||
        p.scope[k].some((v: unknown) => typeof v !== "string"),
    )
  )
    throw new Error("Scope collections must be string arrays.");
  if (
    !Array.isArray(p.steps) ||
    p.steps.some(
      (s: ObjectData) =>
        !s ||
        typeof s.id !== "string" ||
        typeof s.title !== "string" ||
        typeof s.kind !== "string" ||
        !Array.isArray(s.inputs) ||
        s.inputs.some((i) => typeof i !== "string") ||
        !isObject(s.config),
    )
  )
    throw new Error(
      "Steps require ID, title, kind, input IDs and object config.",
    );
  if (
    !isObject(p.agent) ||
    !isObject(p.approval) ||
    !Array.isArray(p.approval.required_signers) ||
    !Array.isArray(p.approval.roster)
  )
    throw new Error("Missing agent settings or approval roster.");
  if (
    !p.disclosure ||
    !Array.isArray(p.disclosure.recipients) ||
    p.disclosure.recipients.some(
      (r: ObjectData) =>
        !r ||
        typeof r.id !== "string" ||
        typeof r.role !== "string" ||
        !Array.isArray(r.fields) ||
        r.fields.some((f) => typeof f !== "string") ||
        typeof r.allow_evidence !== "boolean" ||
        typeof r.allow_excerpts !== "boolean",
    )
  )
    throw new Error(
      "Recipients require ID, role, fields and evidence/excerpt permissions.",
    );
  return p as Plan;
}
export function isObject(v: unknown): v is ObjectData {
  return !!v && typeof v === "object" && !Array.isArray(v);
}
export interface Difference {
  path: string;
  category: string;
  before: unknown;
  after: unknown;
}
export function semanticDiff(before: Plan, after: Plan): Difference[] {
  const changes: Difference[] = [];
  const categories: Record<string, string> = {
    scope: "Evidence & subject scope",
    criteria: "Decision criteria",
    steps: "Method & capabilities",
    agent: "Model & cost limits",
    disclosure: "Recipient disclosure",
    approval: "Authorization",
    mode: "Privacy mode",
    question: "Purpose",
    title: "Agreement",
    id: "Identity",
  };
  for (const key of new Set([...Object.keys(before), ...Object.keys(after)])) {
    if (pretty(before[key]) !== pretty(after[key]))
      changes.push({
        path: key,
        category: categories[key] ?? "Executable settings",
        before: before[key],
        after: after[key],
      });
  }
  return changes;
}
export const TERMINAL = new Set([
  "completed",
  "complete",
  "succeeded",
  "success",
  "failed",
  "blocked",
  "cancelled",
  "interrupted",
  "canceled",
  "suppressed",
  "released",
  "insufficient_evidence",
]);
export const runActive = (run: Run): boolean => !TERMINAL.has(run.status);
export function executionBlockers(
  plan: Plan,
  saved: SavedPlan | null,
  checked: { snapshot: string; result: Check } | null,
  datasetAvailable: boolean,
): string[] {
  const blockers: string[] = [];
  if (!saved?.digest || pretty(saved.plan) !== pretty(plan))
    blockers.push("Save this exact agreement first.");
  if (!checked || checked.snapshot !== pretty(plan) || !checked.result.valid)
    blockers.push("Pass Check plan for this exact agreement.");
  if (plan.scope.dataset_ids.length !== 1 || !datasetAvailable)
    blockers.push("Select one available dataset manifest.");
  return blockers;
}
export function outcomeLabel(value: string): string {
  return (
    (
      {
        supported: "Supported finding",
        contradicted: "Hypothesis contradicted",
        no_finding: "Qualified negative finding",
        insufficient_evidence: "Insufficient evidence",
        suppressed: "Suppressed · not zero",
        blocked: "Admission blocked",
        failed: "Execution failed",
      } as Record<string, string>
    )[value] ?? value.replaceAll("_", " ")
  );
}
