import { createElement } from "react";
import { act, create, type ReactTestRenderer, type ReactTestInstance } from "react-test-renderer";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";
import { api, pollEvaluation } from "./api";
import { Changes } from "./Views";
import Evaluations, { EvaluationReport } from "./Evaluations";
import { blankPlan } from "./model";
import type { Evaluation } from "./types";
const mounted: ReactTestRenderer[] = [];
const text = (n: ReactTestInstance): string => n.children.map((x) => typeof x === "string" ? x : text(x)).join("");
async function mount(element: Parameters<typeof create>[0]) { let t!: ReactTestRenderer; await act(async () => { t = create(element); }); mounted.push(t); return t; }
const button = (t: ReactTestRenderer, label: string) => t.root.findAllByType("button").find((n) => text(n) === label)!;
async function click(t: ReactTestRenderer, label: string) { await act(async () => { await button(t, label).props.onClick(); }); }
async function change(t: ReactTestRenderer, label: string, value: string) { await act(async () => { t.root.findAllByType("label").find((n) => n.findAllByType("span").some((s) => text(s) === label))!.findAll((n) => ["input", "textarea", "select"].includes(String(n.type)))[0].props.onChange({ target: { value } }); }); }
const response = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status });
const job = (status: Evaluation["status"] = "completed"): Evaluation => ({ evaluation_id: "ev/id", status, plan_digest: "saved-hash", evaluated_plan: blankPlan(), evaluated_digest: "derived-hash", changes: [], include_agent: false, case_limit: 16, seed: 0, evidence_mode: "synthetic_full_trace", metrics: null, report: null });
afterEach(() => { act(() => mounted.splice(0).forEach((t) => t.unmount())); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });
it("sends only draft and instruction and requires explicit dependent-group acceptance", async () => {
  const p = blankPlan(); const proposed = { ...p, question: "New question", scope: { ...p.scope, subjects: ["public"] }, title: "Optional title" };
  const fetcher = vi.fn(async (_url: string, _options?: RequestInit) => response({ plan: proposed, changes: [{ path: "/question", before: p.question, after: proposed.question }], usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15, test_model: true }, verification: { test_model: true } })); vi.stubGlobal("fetch", fetcher);
  const onChange = vi.fn(); const t = await mount(createElement(Changes, { baseline: null, plan: p, onChange, online: true }));
  await change(t, "Authoring instruction", "Clarify public scope"); await click(t, "Request unsigned proposal");
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({ plan: p, instruction: "Clarify public scope" });
  expect(onChange).not.toHaveBeenCalled(); expect(text(t.root)).toContain("TEST MODEL"); expect(button(t, "Apply selected changes to draft").props.disabled).toBe(true);
  const scope = t.root.findAllByType("input").find((n) => n.props.type === "checkbox" && text(n.parent!).includes("scope"))!;
  await act(async () => scope.props.onChange()); await click(t, "Apply selected changes to draft");
  expect(onChange).toHaveBeenCalledWith({ ...p, question: proposed.question, scope: proposed.scope }); expect(fetcher).toHaveBeenCalledTimes(1);
});
it("prevents stale proposal acceptance after a concurrent edit", async () => {
  const p = blankPlan(); const onChange = vi.fn(); const t = await mount(createElement(Changes, { baseline: null, plan: p, onChange }));
  await change(t, "Proposed agreement JSON", JSON.stringify({ ...p, title: "Proposal" })); await click(t, "Compare proposal");
  await act(async () => t.update(createElement(Changes, { baseline: null, plan: { ...p, question: "Concurrent" }, onChange })));
  expect(text(t.root)).toContain("draft changed"); expect(button(t, "Apply selected changes to draft").props.disabled).toBe(true);
});
it("shows provider failure without fallback", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => response({ error: { message: "Verified proposal unavailable." } }, 503)));
  const t = await mount(createElement(Changes, { baseline: null, plan: blankPlan(), onChange: vi.fn(), online: true }));
  await change(t, "Authoring instruction", "Clarify"); await click(t, "Request unsigned proposal"); expect(text(t.root)).toContain("Verified proposal unavailable."); expect(button(t, "Apply selected changes to draft")).toBeUndefined();
});
it("sends exact evaluation body and encoded owner-only get/cancel paths", async () => {
  const fetcher = vi.fn(async (url: string, _options?: RequestInit) => response(url.endsWith("cancel") ? { evaluation_id: "ev/id", status: "cancelled" } : url.endsWith("ev%2Fid") ? job() : { evaluation_id: "ev/id" })); vi.stubGlobal("fetch", fetcher);
  await api.evaluate("plan", true, 4, 12); await api.getEvaluation("ev/id"); await api.cancelEvaluation("ev/id");
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({ plan_id: "plan", include_agent: true, case_limit: 4, seed: 12 }); expect(fetcher.mock.calls[1][0]).toBe("/api/workbench/evaluations/ev%2Fid"); expect(fetcher.mock.calls[2][0]).toBe("/api/workbench/evaluations/ev%2Fid/cancel"); expect(fetcher.mock.calls[2][1]?.body).toBe("{}");
});
it.each(["completed", "failed", "cancelled", "interrupted"] as const)("stops polling at %s", async (status) => {
  const get = vi.spyOn(api, "getEvaluation").mockResolvedValue(job(status)); const callback = vi.fn(); await pollEvaluation("ev/id", callback, new AbortController().signal, 1); expect(get).toHaveBeenCalledTimes(1); expect(callback).toHaveBeenCalledWith(expect.objectContaining({ status, evaluation_id: "ev/id" }));
});
it("aborts polling delay without another fetch", async () => {
  vi.useFakeTimers(); const controller = new AbortController(); const get = vi.spyOn(api, "getEvaluation").mockResolvedValue(job("running")); const polling = pollEvaluation("ev/id", vi.fn(), controller.signal, 100); await Promise.resolve(); controller.abort(); await polling; await vi.runAllTimersAsync(); expect(get).toHaveBeenCalledTimes(1);
});
it("propagates downstream polling failure without invented results", async () => { const callback = vi.fn(); vi.spyOn(api, "getEvaluation").mockRejectedValue(new Error("Runtime unavailable")); await expect(pollEvaluation("ev/id", callback, new AbortController().signal)).rejects.toThrow("Runtime unavailable"); expect(callback).not.toHaveBeenCalled(); });
it("renders failures, abstentions, family denominators and exact evaluated digest", () => {
  const value = job("cancelled"); value.report = { partial: true, cases: [{ criterion_id: "family", investigation_id: "one", status: "unknown", outcome: null, failure: "execution_failed", observed_events: 3 }, { criterion_id: "family", investigation_id: "two", status: "evaluated", outcome: "insufficient_evidence", observed_events: 2 }] };
  const html = renderToStaticMarkup(createElement(EvaluationReport, { value })); for (const s of ["derived-hash", "execution_failed", "1 / 2", "partial", "no real-world or model accuracy claim"]) expect(html).toContain(s);
});
it("gates unchecked saves, changes mode case cap and retains IDs across remount", async () => {
  const storage = new Map<string, string>(); vi.stubGlobal("localStorage", { getItem: (k: string) => storage.get(k) ?? null, setItem: (k: string, v: string) => storage.set(k, v) });
  const p = blankPlan(); const saved = { plan: p, digest: "saved" }; vi.spyOn(api, "loadPlan").mockResolvedValue(saved); const submit = vi.spyOn(api, "evaluate").mockResolvedValue({ evaluation_id: "ev/id" }); vi.spyOn(api, "getEvaluation").mockResolvedValue(job());
  const t = await mount(createElement(Evaluations, { saved, eligible: false, online: true, account: "owner" })); expect(button(t, "Start synthetic evaluation").props.disabled).toBe(true);
  await act(async () => t.update(createElement(Evaluations, { saved, eligible: true, online: true, account: "owner" })));
  await change(t, "Evaluation mode", "agent"); await click(t, "Start synthetic evaluation"); expect(submit).toHaveBeenCalledWith(p.id, true, 4, 0); expect(text(t.root)).toContain("derived-hash");
  const reopened = await mount(createElement(Evaluations, { saved, eligible: true, online: true, account: "owner" })); await click(reopened, "ev/id"); expect(text(reopened.root)).toContain("Evaluation completed");
});
it("cancels real service work and refreshes the retained partial report", async () => {
  const get = vi.spyOn(api, "getEvaluation").mockResolvedValue(job("running")); const cancel = vi.spyOn(api, "cancelEvaluation").mockImplementation(async () => { get.mockResolvedValue(job("cancelled")); return { evaluation_id: "ev/id", status: "cancelled" }; });
  const t = await mount(createElement(Evaluations, { saved: null, eligible: false, online: true, account: "owner" }));
  await change(t, "Open evaluation ID", "ev/id"); await click(t, "Open owner-only evaluation"); await click(t, "Cancel evaluation"); expect(cancel).toHaveBeenCalledWith("ev/id"); expect(text(t.root)).toContain("Evaluation cancelled"); expect(button(t, "Cancel evaluation")).toBeUndefined();
});
it("rejects changed remote saved revision before evaluation submission", async () => {
  const saved = { plan: blankPlan(), digest: "reviewed" }; vi.spyOn(api, "loadPlan").mockResolvedValue({ ...saved, digest: "new-revision" }); const submit = vi.spyOn(api, "evaluate");
  const t = await mount(createElement(Evaluations, { saved, eligible: true, online: true, account: "owner" })); await click(t, "Start synthetic evaluation"); expect(submit).not.toHaveBeenCalled(); expect(text(t.root)).toContain("Saved agreement changed");
});
it("never overlaps evaluation status requests and discards an aborted response", async () => {
  vi.useFakeTimers(); const controller = new AbortController(); let finish!: (value: Evaluation) => void;
  const get = vi.spyOn(api, "getEvaluation").mockImplementation(() => new Promise((resolve) => { finish = resolve; })); const callback = vi.fn();
  const polling = pollEvaluation("ev/id", callback, controller.signal, 100); await vi.advanceTimersByTimeAsync(10000); expect(get).toHaveBeenCalledTimes(1); controller.abort(); finish(job()); await polling; expect(callback).not.toHaveBeenCalled();
});
it("renders capacity failure and does not invent an evaluation ID", async () => {
  const saved = { plan: blankPlan(), digest: "saved" }; vi.spyOn(api, "loadPlan").mockResolvedValue(saved); vi.spyOn(api, "evaluate").mockRejectedValue(new Error("Two evaluations are already active."));
  const t = await mount(createElement(Evaluations, { saved, eligible: true, online: true, account: "owner" })); await click(t, "Start synthetic evaluation"); expect(text(t.root)).toContain("Two evaluations are already active."); expect(text(t.root)).not.toContain("Monitoring");
});
