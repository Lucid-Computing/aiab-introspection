/**
 * Editor state. `plan` is always a valid IR (it has passed validatePlan); `code` is the code
 * editor's text, which may be invalid while the user types. Graph edits rewrite `code`;
 * valid code edits replace `plan` without touching the text the user is typing.
 */
import { AGREEMENT } from "../agreement";
import { completeLayout } from "../plan/layout";
import { samplePlan } from "../plan/samples";
import { type Issue, type Layout, LayoutSchema, type Plan, PlanSchema } from "../plan/types";
import { validatePlan } from "../plan/validate";
import { planToJson, jsonToPlan } from "../plan/json";

export interface State {
  plan: Plan;
  layout: Layout;
  code: string;
  /** Why the current `code` is not applied; empty when it matches `plan`. */
  codeIssues: Issue[];
  selected: string | null;
  /** Whether the plan-as-text panel is shown next to the diagram. */
  codeOpen: boolean;
  sample: string;
  toast: { text: string; kind: "info" | "error"; seq: number } | null;
  /** Bumped when positions change for reasons other than dragging, so the canvas refits. */
  fitSeq: number;
}

export type Action =
  | { type: "setPlan"; plan: Plan; layout?: Layout; select?: string | null }
  /** An edit computed from the current plan; use when several may arrive in one event. */
  | { type: "update"; f: (plan: Plan) => Plan }
  | { type: "loadPlan"; plan: Plan; layout?: Layout; sample?: string }
  | { type: "editCode"; text: string }
  | { type: "applyCode"; text: string }
  | { type: "moveNodes"; layout: Layout }
  | { type: "relayout"; layout: Layout }
  | { type: "select"; id: string | null }
  | { type: "toggleCode" }
  | { type: "toast"; text: string; kind?: "info" | "error" };

export function reducer(s: State, a: Action): State {
  switch (a.type) {
    case "setPlan": {
      const layout = completeLayout(a.plan, a.layout ?? s.layout);
      const selected = a.select !== undefined ? a.select : s.selected;
      return {
        ...s,
        plan: a.plan,
        layout,
        code: planToJson(a.plan),
        codeIssues: [],
        selected: selected && a.plan.nodes.some((n) => n.id === selected) ? selected : null,
      };
    }
    case "update":
      return reducer(s, { type: "setPlan", plan: a.f(s.plan) });
    case "loadPlan":
      return {
        ...s,
        plan: a.plan,
        layout: completeLayout(a.plan, a.layout ?? {}),
        code: planToJson(a.plan),
        codeIssues: [],
        selected: null,
        sample: a.sample ?? s.sample,
        fitSeq: s.fitSeq + 1,
      };
    case "editCode":
      return { ...s, code: a.text };
    case "applyCode": {
      if (a.text !== s.code) return s; // stale (the user kept typing)
      if (!s.codeIssues.length && a.text === planToJson(s.plan)) return s; // already applied
      const r = jsonToPlan(a.text);
      if (!r.ok) return { ...s, codeIssues: r.issues };
      const added = r.plan.nodes.some((n) => !s.layout[n.id]);
      return {
        ...s,
        plan: r.plan,
        layout: completeLayout(r.plan, s.layout),
        codeIssues: [],
        selected: s.selected && r.plan.nodes.some((n) => n.id === s.selected) ? s.selected : null,
        fitSeq: added ? s.fitSeq + 1 : s.fitSeq,
      };
    }
    case "moveNodes":
      return { ...s, layout: { ...s.layout, ...a.layout } };
    case "relayout":
      return { ...s, layout: a.layout, fitSeq: s.fitSeq + 1 };
    case "select":
      return s.selected === a.id ? s : { ...s, selected: a.id };
    case "toggleCode":
      return { ...s, codeOpen: !s.codeOpen, fitSeq: s.fitSeq + 1 };
    case "toast":
      return { ...s, toast: { text: a.text, kind: a.kind ?? "info", seq: (s.toast?.seq ?? 0) + 1 } };
  }
}

const STORAGE_KEY = "aiab-playground/v2";

export function initialState(): State {
  const fresh: State = {
    plan: samplePlan("deployed"),
    layout: {},
    code: "",
    codeIssues: [],
    selected: null,
    codeOpen: false,
    sample: "deployed",
    toast: null,
    fitSeq: 0,
  };
  fresh.layout = completeLayout(fresh.plan, {});
  fresh.code = planToJson(fresh.plan);
  if (AGREEMENT) return fresh; // the server's plan replaces this as soon as it loads
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return fresh;
    const saved = JSON.parse(raw);
    const plan = validatePlan(PlanSchema.parse(saved.plan));
    if (!plan.ok) return fresh;
    const layout = LayoutSchema.safeParse(saved.layout);
    const code = typeof saved.code === "string" ? saved.code : planToJson(plan.plan);
    const parsed = jsonToPlan(code);
    return {
      ...fresh,
      plan: plan.plan,
      layout: completeLayout(plan.plan, layout.success ? layout.data : {}),
      code,
      codeIssues: parsed.ok ? [] : parsed.issues,
      codeOpen: saved.codeOpen === true,
      sample: typeof saved.sample === "string" ? saved.sample : fresh.sample,
    };
  } catch {
    return fresh; // an older or corrupt save must not brick the page
  }
}

export function persist(s: State): void {
  if (AGREEMENT) return; // an agreement lives on the server, never in this browser
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ plan: s.plan, layout: s.layout, code: s.code, codeOpen: s.codeOpen, sample: s.sample }),
    );
  } catch {
    // storage full or blocked: the page still works, it just will not survive a refresh
  }
}
