import { createElement, type ReactNode } from "react";
import { act, create, type ReactTestInstance, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, expect, it, vi } from "vitest";
import WorkbenchApp from "./WorkbenchApp";
import { Changes, GraphView } from "./Views";
import { api } from "./api";
import Evaluations from "./Evaluations";
import Results from "./Results";
import type { Evaluation } from "./types";
import { blankPlan } from "./model";

// Stub the DOM-dependent graph canvas only; the real graph inspector stays mounted.
vi.mock("@xyflow/react", () => ({
  ReactFlowProvider: ({ children }: { children: ReactNode }) => children,
  ReactFlow: () => null, Background: () => null, Controls: () => null,
}));

const mounted: ReactTestRenderer[] = [];
const text = (n: ReactTestInstance): string => n.children.map(c => typeof c === "string" ? c : text(c)).join("");
const button = (t: ReactTestRenderer, label: string) => t.root.findAllByType("button").find(n => text(n) === label)!;
const input = (t: ReactTestRenderer, label: string) => t.root.findAllByType("label").find(n => n.findAllByType("span").some(s => text(s) === label))!.findAll(n => ["input", "textarea", "select"].includes(String(n.type)))[0];
async function mount(element: Parameters<typeof create>[0]) { let t!: ReactTestRenderer; await act(async () => { t = create(element); }); mounted.push(t); return t; }
async function click(t: ReactTestRenderer, label: string) { await act(async () => { await button(t, label).props.onClick(); }); }
async function change(t: ReactTestRenderer, label: string, value: string) { await act(async () => { input(t, label).props.onChange({ target: { value } }); }); }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; }
async function app() {
  vi.stubGlobal("window", new EventTarget());
  const p = blankPlan(); p.id = "sol-tester-component";
  p.steps = [{ id: "measure", title: "Measure", kind: "rules", inputs: [], config: { rule: "synthetic" } }];
  vi.spyOn(api, "bootstrap").mockResolvedValue({ user: { id: "tester" }, capabilities: {}, audits: [{ id: "audit", title: "Synthetic catalog", question: "Question", plan: p }], datasets: [], plans: [] });
  return { t: await mount(createElement(WorkbenchApp)), p };
}
afterEach(() => { act(() => mounted.splice(0).forEach(t => t.unmount())); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("WB-B02: retains unapplied code across Agreement/Code navigation", async () => {
  const { t } = await app(); await click(t, "Code");
  const code = t.root.findByProps({ id: "wb-plan-code" });
  const edited = JSON.stringify({ ...JSON.parse(code.props.value), title: "Keep my code draft" });
  await act(async () => code.props.onChange({ target: { value: edited } }));
  expect(text(t.root)).toContain("Unapplied code is excluded");
  await click(t, "Agreement"); await click(t, "Code");
  expect(t.root.findByProps({ id: "wb-plan-code" }).props.value).toBe(edited);
});
it("WB-B02: prevents agreement replacement when only the code buffer is dirty", async () => {
  const { t } = await app(); await click(t, "Code");
  const code = t.root.findByProps({ id: "wb-plan-code" });
  await act(async () => code.props.onChange({ target: { value: "{ unfinished code" } }));
  await click(t, "+ New agreement");
  expect(t.root.findAllByProps({ id: "wb-plan-code" })).toHaveLength(1);
});
it("WB-B02: retains unapplied scope settings across tabs", async () => {
  const { t } = await app(); const edited = '{"subjects":["synthetic-person"]}';
  await change(t, "Complete scope contract", edited);
  await click(t, "Code"); await click(t, "Agreement");
  expect(input(t, "Complete scope contract").props.value).toBe(edited);
});
it("excludes unapplied code from save, as explicitly documented in the interface", async () => {
  const { t } = await app(); const save = vi.spyOn(api, "savePlan").mockImplementation(async p => ({ plan: p, digest: "saved" }));
  await click(t, "Code"); const code = t.root.findByProps({ id: "wb-plan-code" }); const original = JSON.parse(code.props.value);
  await act(async () => code.props.onChange({ target: { value: JSON.stringify({ ...original, title: "Unapplied" }) } }));
  expect(button(t, "Save agreement").props.disabled).toBe(true);
  await click(t, "Save agreement"); expect(save).not.toHaveBeenCalled();
});
it("preserves edits and shows a conflict when optimistic save is rejected", async () => {
  const { t } = await app(); vi.spyOn(api, "savePlan").mockRejectedValue(new Error("Version conflict: reload required"));
  await change(t, "Agreement title", "Keep conflicting draft"); await click(t, "Save agreement");
  expect(input(t, "Agreement title").props.value).toBe("Keep conflicting draft");
  expect(text(t.root)).toContain("Version conflict: reload required"); expect(text(t.root)).toContain("Unsaved changes");
});
it("blocks a late assisted response after a concurrent edit without saving or running", async () => {
  const p = blankPlan(); const onChange = vi.fn(); const request = deferred<Awaited<ReturnType<typeof api.suggest>>>();
  vi.spyOn(api, "suggest").mockReturnValue(request.promise); const save = vi.spyOn(api, "savePlan"); const run = vi.spyOn(api, "run");
  const t = await mount(createElement(Changes, { baseline: null, plan: p, onChange, online: true }));
  await change(t, "Authoring instruction", "Change the title"); let pending!: Promise<void>;
  await act(async () => { pending = button(t, "Request unsigned proposal").props.onClick(); });
  await act(async () => t.update(createElement(Changes, { baseline: null, plan: { ...p, question: "Concurrent question" }, onChange, online: true })));
  await act(async () => { request.resolve({ plan: { ...p, title: "Late proposal" }, changes: [], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2, test_model: true }, verification: { test_model: true } }); await pending; });
  expect(text(t.root)).toContain("draft changed while this proposal was pending"); expect(button(t, "Apply selected changes to draft").props.disabled).toBe(true);
  expect(onChange).not.toHaveBeenCalled(); expect(save).not.toHaveBeenCalled(); expect(run).not.toHaveBeenCalled();
});
it("graph inspector edits the same plan and preserves config and extension fields", async () => {
  const p = blankPlan(); p.extension = { retain: true }; p.steps = [{ id: "read", kind: "select", title: "Read", inputs: [], config: { query: "synthetic" } }];
  const onChange = vi.fn(); const t = await mount(createElement(GraphView, { plan: p, selected: "read", onSelect: vi.fn(), onChange }));
  await change(t, "Step title", "Readable step"); expect(onChange).toHaveBeenCalledWith({ ...p, steps: [{ ...p.steps[0], title: "Readable step" }] });
});
it("WB-B01: binds run and evaluation requests to a reviewed saved digest", async () => {
  const fetcher = vi.fn(async (_url: string, _options?: RequestInit) => new Response(JSON.stringify({ run_id: "run", evaluation_id: "evaluation" }))); vi.stubGlobal("fetch", fetcher);
  await api.run("saved-plan", "northstar", "rehearse", "reviewed-A");
  await api.run("saved-plan", "northstar", "audit", "reviewed-A");
  await api.evaluate("saved-plan", false, 1, 0, "reviewed-A");
  const bodies = fetcher.mock.calls.map(c => JSON.parse(String(c[1]?.body)));
  expect(bodies.map(b => b.expected_digest)).toEqual(["reviewed-A", "reviewed-A", "reviewed-A"]);
});


it("blocks evaluation if the local saved revision changes during the preflight request", async () => {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: vi.fn() });
  const saved = { plan: blankPlan(), digest: "revision-A" };
  const request = deferred<typeof saved>(); vi.spyOn(api, "loadPlan").mockReturnValue(request.promise);
  const evaluate = vi.spyOn(api, "evaluate");
  const props = { saved, eligible: true, online: true, account: "tester" };
  const t = await mount(createElement(Evaluations, props));
  let pending!: Promise<void>;
  await act(async () => { pending = button(t, "Start synthetic evaluation").props.onClick(); });
  await act(async () => t.update(createElement(Evaluations, { ...props, saved: { plan: { ...saved.plan, title: "New revision" }, digest: "revision-B" } })));
  await act(async () => { request.resolve(saved); await pending; });
  expect(evaluate).not.toHaveBeenCalled(); expect(text(t.root)).toContain("Saved agreement changed");
});
it("preserves active evaluation and cancellation controls after cancellation is rejected", async () => {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: vi.fn() });
  const value: Evaluation = { evaluation_id: "sol-test-job", status: "running", plan_digest: "A", evaluated_digest: "A", evaluated_plan: blankPlan(), changes: [], include_agent: false, case_limit: 1, seed: 0, evidence_mode: "synthetic_full_trace", metrics: null, report: null };
  vi.spyOn(api, "getEvaluation").mockResolvedValue(value);
  vi.spyOn(api, "cancelEvaluation").mockRejectedValue(new Error("Cancellation unavailable"));
  const t = await mount(createElement(Evaluations, { saved: null, eligible: false, online: true, account: "tester" }));
  await change(t, "Open evaluation ID", "sol-test-job"); await click(t, "Open owner-only evaluation");
  await click(t, "Cancel evaluation");
  expect(text(t.root)).toContain("Cancellation unavailable"); expect(text(t.root)).toContain("Evaluation running");
  expect(button(t, "Cancel evaluation").props.disabled).toBe(false);
});
it("source lookup failure retains distinct counterevidence and insufficient-evidence context", async () => {
  vi.spyOn(api, "evidence").mockRejectedValue(new Error("Scoped record unavailable"));
  const t = await mount(createElement(Results, { run: { run_id: "sol-run", status: "completed", mode: "rehearse", result: { findings: [{ id: "f", criterion_id: "scope", title: "Synthetic gap", outcome: "insufficient_evidence", claim: "Coverage is incomplete", method: "rules", evidence_ids: ["support"], counterevidence_ids: ["counter"], limitations: ["Missing interval"], details: {} }], coverage: { missing_sources: ["synthetic-source"] } } }, busy: false, onCancel: vi.fn(), onRefresh: vi.fn(), onChallenge: vi.fn() }));
  await click(t, "counter");
  for (const phrase of ["Scoped record unavailable", "Insufficient evidence", "Counterevidence", "Missing interval", "synthetic-source"]) expect(text(t.root)).toContain(phrase);
  expect(button(t, "support")).toBeDefined(); expect(button(t, "counter").props.disabled).toBe(false);
});

it("retains invalid code after failed apply and requires explicit reset before a new agreement", async () => {
  const { t } = await app(); await click(t, "Code");
  await act(async () => t.root.findByProps({ id: "wb-plan-code" }).props.onChange({ target: { value: "{ incomplete" } }));
  await click(t, "Apply code to document");
  expect(t.root.findByProps({ id: "wb-plan-code" }).props.value).toBe("{ incomplete");
  await click(t, "+ New agreement");
  expect(text(t.root)).toContain("Save or discard");
  await click(t, "Reset code buffer");
  expect(button(t, "Save agreement").props.disabled).toBe(false);
  await click(t, "+ New agreement");
  expect(t.root.findAllByProps({ id: "wb-plan-code" })).toHaveLength(0);
});

it("retains rejected settings across tabs, blocks save and clears them on explicit discard", async () => {
  const { t } = await app(); const save = vi.spyOn(api, "savePlan");
  await change(t, "Complete scope contract", "{ invalid");
  const scope = t.root.findAllByProps({ className: "wb-json-editor" }).find(n => text(n).includes("Complete scope contract"))!;
  await act(async () => scope.findAllByType("button").find(n => text(n) === "Apply settings")!.props.onClick());
  await click(t, "Code"); await click(t, "Agreement");
  expect(input(t, "Complete scope contract").props.value).toBe("{ invalid");
  expect(button(t, "Save agreement").props.disabled).toBe(true);
  expect(button(t, "02 / Rehearse actual methods").props.disabled).toBe(true);
  await click(t, "Save agreement"); expect(save).not.toHaveBeenCalled();
  await click(t, "Discard changes");
  expect(() => JSON.parse(input(t, "Complete scope contract").props.value)).not.toThrow();
  expect(button(t, "Save agreement").props.disabled).toBe(false);
});

it("preserves code bytes on parent edits and requires explicit conflict resolution", async () => {
  const { t } = await app(); await click(t, "Code");
  const initial = t.root.findByProps({ id: "wb-plan-code" }).props.value;
  const typed = JSON.stringify({ ...JSON.parse(initial), title: "Typed title" });
  await act(async () => t.root.findByProps({ id: "wb-plan-code" }).props.onChange({ target: { value: typed } }));
  await click(t, "Agreement"); await change(t, "Agreement title", "Parent title"); await click(t, "Code");
  expect(t.root.findByProps({ id: "wb-plan-code" }).props.value).toBe(typed);
  expect(text(t.root)).toContain("Document changed beneath this code buffer");
  expect(button(t, "Apply code to document").props.disabled).toBe(true);
  await click(t, "Keep code buffer against current document");
  await click(t, "Apply code to document"); await click(t, "Agreement");
  expect(input(t, "Agreement title").props.value).toBe("Typed title");
});

it("protects unload for buffer-only edits and removes the guard after reset", async () => {
  const { t } = await app(); await click(t, "Code");
  await act(async () => t.root.findByProps({ id: "wb-plan-code" }).props.onChange({ target: { value: "{" } }));
  const event = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(true);
  await click(t, "Reset code buffer");
  const clean = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(clean);
  expect(clean.defaultPrevented).toBe(false);
});

it("does not repopulate a new agreement with a late proposal from the previous agreement", async () => {
  const { t } = await app(); await click(t, "Changes");
  const source = t.root.findByType(Changes).props.plan;
  const request = deferred<Awaited<ReturnType<typeof api.suggest>>>();
  vi.spyOn(api, "suggest").mockReturnValue(request.promise);
  await change(t, "Authoring instruction", "Change title");
  let pending!: Promise<void>;
  await act(async () => { pending = button(t, "Request unsigned proposal").props.onClick(); });
  await click(t, "+ New agreement");
  const title = input(t, "Agreement title").props.value;
  await act(async () => {
    request.resolve({ plan: { ...source, title: "Old late proposal" }, changes: [], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2, test_model: true }, verification: {} });
    await pending;
  });
  expect(input(t, "Agreement title").props.value).toBe(title);
  await click(t, "Changes");
  expect(button(t, "Apply selected changes to draft")).toBeUndefined();
});

it("requires explicit settings reconciliation after a parent scope edit and supports buffer reset", async () => {
  const { t } = await app();
  await change(t, "Complete scope contract", '{"subjects":["typed"]}');
  await change(t, "Subjects / identity-map references", "parent");
  const scope = t.root.findAllByProps({ className: "wb-json-editor" }).find(n => text(n).includes("Complete scope contract"))!;
  expect(text(scope)).toContain("Document changed beneath this settings buffer");
  expect(scope.findAllByType("button").find(n => text(n) === "Apply settings")!.props.disabled).toBe(true);
  await act(async () => scope.findAllByType("button").find(n => text(n) === "Reset settings buffer")!.props.onClick());
  expect(JSON.parse(input(t, "Complete scope contract").props.value).subjects).toEqual(["parent"]);
  expect(button(t, "Save agreement").props.disabled).toBe(false);
});
