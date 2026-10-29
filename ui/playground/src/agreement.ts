/**
 * Agreement mode: the builder embedded in the agreement UI, at
 * `/audit-plan-playground/?agreement=<id>[&readonly=1]`. The plan comes from the server and
 * goes back to it; nothing is kept in this browser. The server runs the box's own loader
 * on every save and answers with the loader's issues when it refuses. Without the
 * parameter the page is the standalone playground: samples, localStorage, no server.
 */
import { type Plan, PlanSchema } from "./plan/types";
import { validatePlan } from "./plan/validate";

const params = new URLSearchParams(window.location.search);
/** The agreement id, or null in standalone mode. */
export const AGREEMENT: string | null = params.get("agreement");
export const READONLY = params.get("readonly") === "1";

export interface ServerPlan {
  plan: Plan;
  digest: string;
  editable: boolean;
  frozen: boolean;
  issue: string | null;
  issues: ServerIssue[];
}
export interface ServerIssue {
  message: string;
  node?: string | null;
  path?: (string | number)[];
}

const url = (id: string) => `/a/${encodeURIComponent(id)}/plan.json`;

export async function loadAgreement(id: string): Promise<ServerPlan> {
  const r = await fetch(url(id), { credentials: "same-origin", headers: { Accept: "application/json" } });
  if (r.status === 401) throw new Error("Your session expired. Sign in again, then reload this page.");
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error ?? `The server answered ${r.status}`);
  const parsed = PlanSchema.safeParse(body.plan);
  if (!parsed.success) throw new Error(`The saved plan does not parse: ${parsed.error.issues[0]?.message}`);
  const checked = validatePlan(parsed.data);
  if (!checked.ok) throw new Error(`The saved plan is not a valid graph: ${checked.issues[0]?.message}`);
  return {
    plan: checked.plan,
    digest: String(body.digest),
    editable: Boolean(body.editable),
    frozen: Boolean(body.frozen),
    issue: body.issue ?? null,
    issues: Array.isArray(body.issues) ? body.issues : [],
  };
}

export type SaveResult = { ok: true; digest: string } | { ok: false; message: string; issues: ServerIssue[] };

export async function saveAgreement(id: string, plan: Plan, expect: string): Promise<SaveResult> {
  const r = await fetch(url(id), {
    method: "PUT",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ plan, expect }),
  });
  const body = await r.json().catch(() => ({}));
  if (r.ok) return { ok: true, digest: String(body.digest) };
  if (r.status === 401) return { ok: false, message: "Your session expired. Sign in again, then save.", issues: [] };
  return { ok: false, message: body.error ?? `The server answered ${r.status}`, issues: body.issues ?? [] };
}
