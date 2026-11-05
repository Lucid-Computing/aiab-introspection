import type { Bootstrap, Check, Dataset, Plan, Run, SavedPlan } from "./types";
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
  approve: (planId: string, digest: string, user: unknown, assertCurrent: () => void | Promise<void>) =>
    signApproval(post, planId, digest, user, assertCurrent),
  revoke: (planId: string, digest: string) =>
    post<Object>(`/plans/${id(planId)}/revoke`, { digest }),
  challenge: (runId: string, challenge: unknown) =>
    post<Object>(`/runs/${id(runId)}/challenge`, challenge),
  history: () =>
    request<{ history?: unknown[]; records?: unknown[] }>("/history"),
};

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
