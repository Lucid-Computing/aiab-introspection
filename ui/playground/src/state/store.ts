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
  /** Undo history of plan edits, oldest first, for this page visit only. */
  past: Snapshot[];
  /** Redo steps, the next one first. Any new edit clears them. */
  future: Snapshot[];
  /** Group of the edit on top of `past`; a following edit of the same group joins it. */
  lastGroup: string | null;
}

/** One history step. Positions ride along so an undone delete puts the block back where it was. */
export interface Snapshot {
  plan: Plan;
  layout: Layout;
}

/** Undo steps kept. */
export const HISTORY_LIMIT = 100;

export type Action = (
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
  | { type: "toast"; text: string; kind?: "info" | "error" }
  | { type: "undo" }
  | { type: "redo" }
) & {
  /** Edits with the same group make one undo step: one typing session in a field, or the
   *  several changes one key press or drop sends. Set by the dispatch wrapper in App. */
  group?: string;
};

const snap = (s: State): Snapshot => ({ plan: s.plan, layout: s.layout });

/** Puts back a history step's plan. Blocks still on the diagram keep where they are now. */
function restore(s: State, to: Snapshot): State {
  return {
    ...s,
    plan: to.plan,
    layout: completeLayout(to.plan, { ...to.layout, ...s.layout }),
    code: planToJson(to.plan),
    codeIssues: [],
    selected: s.selected && to.plan.nodes.some((n) => n.id === s.selected) ? s.selected : null,
    lastGroup: null,
  };
}

/**
 * The editor's reducer with undo history. Any action that changes the plan records a step;
 * moving blocks does not, because positions are not part of the saved plan. Loading a plan
 * starts a new history.
 */
export function reducer(s: State, a: Action): State {
  if (a.type === "undo" || a.type === "redo") {
    // Skip steps that would change nothing, e.g. a field the user already undid in place.
    const now = planToJson(s.plan);
    let past = s.past;
    let future = s.future;
    if (a.type === "undo") {
      while (past.length && planToJson(past[past.length - 1].plan) === now) past = past.slice(0, -1);
      const to = past[past.length - 1];
      if (!to) return past === s.past ? s : { ...s, past, lastGroup: null };
      return { ...restore(s, to), past: past.slice(0, -1), future: [snap(s), ...future] };
    }
    while (future.length && planToJson(future[0].plan) === now) future = future.slice(1);
    const to = future[0];
    if (!to) return future === s.future ? s : { ...s, future };
    return { ...restore(s, to), past: [...past, snap(s)], future: future.slice(1) };
  }
  const n = edit(s, a);
  if (a.type === "loadPlan") return { ...n, past: [], future: [], lastGroup: null };
  if (n.plan === s.plan || planToJson(n.plan) === planToJson(s.plan)) return n;
  if (a.group && a.group === s.lastGroup && s.past.length) {
    // The same typing session or event: it joins the step on top, which goes if it is undone.
    const top = s.past[s.past.length - 1];
    if (planToJson(top.plan) === planToJson(n.plan)) return { ...n, past: s.past.slice(0, -1), future: [], lastGroup: null };
    return { ...n, future: [] };
  }
  return {
    ...n,
    past: [...s.past, snap(s)].slice(-HISTORY_LIMIT),
    future: [],
    lastGroup: a.group ?? null,
  };
}

function edit(s: State, a: Action): State {
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
      return edit(s, { type: "setPlan", plan: a.f(s.plan) });
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
    case "undo":
    case "redo":
      return s; // handled by reducer
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
    past: [],
    future: [],
    lastGroup: null,
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
