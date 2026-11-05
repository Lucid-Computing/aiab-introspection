import type { Bootstrap, Check, Dataset, Evaluation, Plan, Run, SavedPlan, Suggestion } from "./types";
import { signApproval } from "./consent";
import { runActive } from "./model";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}
export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`/api/workbench${path}`, {
    ...options,
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  const raw = await response.text();
  let body;
  try {
    body = JSON.parse(raw);
  } catch {
    throw new ApiError(
      response.status,
      "unavailable",
      "Workbench API unavailable: expected JSON. Check the backend route and cookie login.",
    );
  }
  if (!response.ok)
    throw new ApiError(
      response.status,
      body?.error?.code ?? "request_failed",
      body?.error?.message ?? `Request failed (${response.status}).`,
    );
  if (!body || typeof body !== "object" || Array.isArray(body))
    throw new ApiError(
      response.status,
      "invalid_response",
      "Workbench API returned an invalid response.",
    );
  return body as T;
}
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
const id = encodeURIComponent;
export const api = {
  evidence: (runId: string, eventId: string) => request<{event: Record<string, unknown>; origin: string}>(`/runs/${id(runId)}/evidence/${id(eventId)}`),
  suggest: (plan: Plan, instruction: string) => post<Suggestion>("/suggestions", { plan, instruction }),
  evaluate: (plan_id: string, include_agent: boolean, case_limit: number, seed: number) =>
    post<{ evaluation_id: string }>("/evaluations", { plan_id, include_agent, case_limit, seed }),
  getEvaluation: async (evaluationId: string, signal?: AbortSignal) => {
    const value = await request<Evaluation>(`/evaluations/${id(evaluationId)}`, { signal });
    if (!EVALUATION_STATUSES.has(value.status) || value.evaluation_id !== evaluationId)
      throw new ApiError(200, "invalid_response", "Evaluation response is invalid; state is unknown.");
    return value;
  },
  cancelEvaluation: (evaluationId: string) => post<{ evaluation_id: string; status: Evaluation["status"] }>(`/evaluations/${id(evaluationId)}/cancel`, {}),
  bootstrap: (signal?: AbortSignal) =>
    request<Bootstrap>("/bootstrap", { signal }),
  catalog: () => request<{ audits: Bootstrap["audits"] }>("/catalog"),
  datasets: () => request<{ datasets: Dataset[] }>("/datasets"),
  importDataset: (dataset: unknown) =>
    post<{ dataset?: Dataset }>("/datasets", { dataset }),
  loadPlan: (planId: string) => request<SavedPlan>(`/plans/${id(planId)}`),
  savePlan: (plan: Plan, digest?: string) =>
    digest
      ? request<SavedPlan>(`/plans/${id(plan.id)}`, {
          method: "PUT",
          body: JSON.stringify({ plan, expected_digest: digest }),
        })
      : post<SavedPlan>("/plans", { plan }),
  check: (plan: Plan) => post<Check>("/check", { plan }),
  run: (planId: string, datasetId: string, mode: "audit" | "rehearse") =>
    post<{ run_id: string }>("/runs", {
      plan_id: planId,
      dataset_id: datasetId,
      mode,
    }),
  getRun: async (runId: string, signal?: AbortSignal) => {
    const value = await request<Run>(`/runs/${id(runId)}`, { signal });
    if (typeof value.status !== "string" || !value.status)
      throw new ApiError(
        200,
        "invalid_response",
        "Run response has no status; execution state is unknown.",
      );
    return value;
  },
  cancel: (runId: string) => post<Object>(`/runs/${id(runId)}/cancel`),
  approve: (
    planId: string,
    digest: string,
    user: unknown,
    assertCurrent: () => void | Promise<void>,
  ) => signApproval(post, planId, digest, user, assertCurrent),
  revoke: (planId: string, digest: string) =>
    post<Object>(`/plans/${id(planId)}/revoke`, { digest }),
  challenge: (runId: string, challenge: unknown) =>
    post<Object>(`/runs/${id(runId)}/challenge`, challenge),
  bind: (planId: string) =>
    post<{ bound: boolean; digest: string }>(`/plans/${id(planId)}/bind`),
  readiness: (planId: string) =>
    request<{ ready: boolean }>(`/plans/${id(planId)}/readiness`),
  reviews: (planId: string, digest: string) =>
    request<{ reviews: Record<string, unknown>[] }>(
      `/plans/${id(planId)}/reviews?digest=${id(digest)}`,
    ),
  review: (
    planId: string,
    body: {
      digest: string;
      clause: string;
      note: string;
      recipients?: string[];
    },
  ) => post<Record<string, unknown>>(`/plans/${id(planId)}/reviews`, body),
  challenges: (runId: string) =>
    request<{ challenges: Record<string, unknown>[] }>(
      `/runs/${id(runId)}/challenges`,
    ),
  release: (runId: string) =>
    post<{ projection: NonNullable<Run["result"]>; receipt: unknown }>(
      `/runs/${id(runId)}/release`,
    ),
  history: () =>
    request<{ history?: unknown[]; records?: unknown[] }>("/history"),
};

const EVALUATION_STATUSES = new Set(["queued", "running", "completed", "failed", "cancelled", "interrupted"]);
export const evaluationActive = (value: Evaluation) => value.status === "queued" || value.status === "running";
export async function pollEvaluation(evaluationId: string, onValue: (value: Evaluation) => void, signal: AbortSignal, interval = 1500) {
  let delay = interval;
  while (!signal.aborted) {
    const value = await api.getEvaluation(evaluationId, signal);
    if (signal.aborted) return;
    onValue(value);
    if (!evaluationActive(value)) return;
    await new Promise<void>((resolve) => {
      const done = () => { clearTimeout(timer); signal.removeEventListener("abort", done); resolve(); };
      const timer = setTimeout(done, delay);
      signal.addEventListener("abort", done, { once: true });
    });
    delay = Math.min(delay * 1.2, 5000);
  }
}

/** Poll only the product endpoint, serially, with cancellation and bounded backoff. */
export async function pollRun(
  runId: string,
  onRun: (run: Run) => void,
  signal: AbortSignal,
  interval = 1500,
): Promise<void> {
  let delay = interval;
  while (!signal.aborted) {
    const run = await api.getRun(runId, signal);
    if (signal.aborted) return;
    onRun(run);
    if (!runActive(run)) return;
    await new Promise<void>((resolve) => {
      const done = () => {
        clearTimeout(timer);
        signal.removeEventListener("abort", done);
        resolve();
      };
      const timer = setTimeout(done, delay);
      signal.addEventListener("abort", done, { once: true });
    });
    delay = Math.min(delay * 1.2, 5000);
  }
}
