/**
 * Editor state. `plan` is always a valid IR (it has passed validatePlan); `yaml` is the code
 * editor's text, which may be invalid while the user types. Graph edits rewrite `yaml`;
 * valid code edits replace `plan` without touching the text the user is typing.
 */
import { AGREEMENT } from "../agreement";
import { completeLayout } from "../plan/layout";
import { samplePlan } from "../plan/samples";
import { type Issue, type Layout, LayoutSchema, type Plan, PlanSchema } from "../plan/types";
import { validatePlan } from "../plan/validate";
import { planToYaml, yamlToPlan } from "../plan/yaml";

export type View = "graph" | "code" | "split";

export interface State {
  plan: Plan;
  layout: Layout;
  yaml: string;
  /** Why the current `yaml` is not applied; empty when it matches `plan`. */
  codeIssues: Issue[];
  selected: string | null;
  view: View;
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
  | { type: "editYaml"; text: string }
  | { type: "applyYaml"; text: string }
  | { type: "moveNodes"; layout: Layout }
  | { type: "relayout"; layout: Layout }
  | { type: "select"; id: string | null }
  | { type: "view"; view: View }
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
        yaml: planToYaml(a.plan),
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
        yaml: planToYaml(a.plan),
        codeIssues: [],
        selected: null,
        sample: a.sample ?? s.sample,
        fitSeq: s.fitSeq + 1,
      };
    case "editYaml":
      return { ...s, yaml: a.text };
    case "applyYaml": {
      if (a.text !== s.yaml) return s; // stale (the user kept typing)
      if (!s.codeIssues.length && a.text === planToYaml(s.plan)) return s; // already applied
      const r = yamlToPlan(a.text);
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
    case "view":
      return { ...s, view: a.view, fitSeq: s.fitSeq + 1 };
    case "toast":
      return { ...s, toast: { text: a.text, kind: a.kind ?? "info", seq: (s.toast?.seq ?? 0) + 1 } };
  }
}

const STORAGE_KEY = "aiab-playground/v1";

export function initialState(): State {
  const fresh: State = {
    plan: samplePlan("deployed"),
    layout: {},
    yaml: "",
    codeIssues: [],
    selected: null,
    view: "split",
    sample: "deployed",
    toast: null,
    fitSeq: 0,
  };
  fresh.layout = completeLayout(fresh.plan, {});
  fresh.yaml = planToYaml(fresh.plan);
  if (AGREEMENT) return fresh; // the server's plan replaces this as soon as it loads
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return fresh;
    const saved = JSON.parse(raw);
    const plan = validatePlan(PlanSchema.parse(saved.plan));
    if (!plan.ok) return fresh;
    const layout = LayoutSchema.safeParse(saved.layout);
    const yaml = typeof saved.yaml === "string" ? saved.yaml : planToYaml(plan.plan);
    const parsed = yamlToPlan(yaml);
    return {
      ...fresh,
      plan: plan.plan,
      layout: completeLayout(plan.plan, layout.success ? layout.data : {}),
      yaml,
      codeIssues: parsed.ok ? [] : parsed.issues,
      view: ["graph", "code", "split"].includes(saved.view) ? saved.view : fresh.view,
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
      JSON.stringify({ plan: s.plan, layout: s.layout, yaml: s.yaml, view: s.view, sample: s.sample }),
    );
  } catch {
    // storage full or blocked: the page still works, it just will not survive a refresh
  }
}
