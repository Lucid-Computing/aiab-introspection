import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, pollRun, request } from "./api";
import {
  blankPlan,
  describeStep,
  executionBlockers,
  localIssues,
  outcomeLabel,
  parsePlan,
  pretty,
  runActive,
  semanticDiff,
} from "./model";
import Results, { RecipientPreview } from "./Results";
import Editor from "./Editor";
import WorkbenchApp from "./WorkbenchApp";
import type { Finding, Run } from "./types";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});
const response = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const finding: Finding = {
  id: "f-1",
  criterion_id: "scope",
  title: "Network action",
  outcome: "supported",
  claim: "An executed action crossed the boundary.",
  evidence_ids: ["event-support"],
  counterevidence_ids: ["event-exception"],
  limitations: ["One collector interval is missing."],
  method: "rules",
  details: { claim_type: "observed fact" },
};
function results(run: Run | null) {
  return renderToStaticMarkup(
    createElement(Results, {
      run,
      onCancel: vi.fn(),
      onRefresh: vi.fn(),
      onChallenge: vi.fn().mockResolvedValue(undefined),
      busy: false,
    }),
  );
}

describe("v1 plan authoring contract", () => {
  it("binds execution readiness to the exact saved and checked plan; scope, policy and method edits invalidate it", () => {
    const plan = blankPlan();
    plan.scope.dataset_ids = ["dataset"];
    const saved = { plan: structuredClone(plan), digest: "signed-plan-digest" };
    const checked = {
      snapshot: pretty(plan),
      result: { valid: true, diagnostics: [], requirements: {} },
    };
    expect(executionBlockers(plan, saved, checked, true)).toEqual([]);
    for (const mutate of [
      (p: typeof plan) => {
        p.scope.subjects.push("another-subject");
      },
      (p: typeof plan) => {
        p.disclosure.min_group_size = 10;
      },
      (p: typeof plan) => {
        p.steps.push({
          id: "new",
          title: "Investigate",
          kind: "agent",
          inputs: [],
          config: {},
        });
      },
    ]) {
      const next = structuredClone(plan);
      mutate(next);
      expect(executionBlockers(next, saved, checked, true)).toEqual([
        "Save this exact agreement first.",
        "Pass Check plan for this exact agreement.",
      ]);
    }
    expect(executionBlockers(plan, null, checked, true)).toContain(
      "Save this exact agreement first.",
    );
    expect(
      executionBlockers(
        plan,
        saved,
        { ...checked, result: { ...checked.result, valid: false } },
        true,
      ),
    ).toContain("Pass Check plan for this exact agreement.");
    expect(executionBlockers(plan, saved, checked, false)).toContain(
      "Select one available dataset manifest.",
    );
  });
  it("round-trips clauses, graph inputs, custom code and future backend fields without stripping them", () => {
    const plan = blankPlan();
    plan.scope.dataset_ids = ["northstar"];
    plan.question = "Was the scope respected?";
    plan.criteria[0].description =
      "Apply contemporaneous policy; missing execution evidence is inconclusive.";
    plan.steps = [
      {
        id: "find",
        kind: "select",
        title: "Find",
        inputs: [],
        config: { source: "network" },
      },
      {
        id: "calculate",
        kind: "python",
        title: "Compute",
        inputs: ["find"],
        config: {
          code: "result = len(events)",
          output_schema: { type: "integer" },
        },
      },
    ];
    plan.approval.retention = { days: 7 };
    plan.resource_commitments = [{ digest: "abc" }];
    const parsed = parsePlan(pretty(plan));
    expect(parsed).toEqual(plan);
    expect(localIssues(parsed)).toEqual([]);
    expect(describeStep(parsed.steps[1])).toContain("custom python");
    expect(describeStep(parsed.steps[1])).toContain(
      "full meaning is not inferred",
    );
  });
  it("does not treat unspecified subjects or a blank plan as authorization", () => {
    const plan = blankPlan();
    expect(localIssues(plan)).toContain(
      "Choose an evidence dataset. Unspecified scope grants no access.",
    );
    const html = renderToStaticMarkup(
      createElement(Editor, {
        plan,
        onChange: vi.fn(),
        selected: "",
        onSelect: vi.fn(),
      }),
    );
    expect(html).toContain("Empty collections leave scope unspecified");
    expect(html).toContain(
      "personal mode still requires the authenticated subject",
    );
  });
  it("detects forward references, cycles and duplicate IDs rather than silently repairing the method", () => {
    const plan = blankPlan();
    plan.steps = [
      { id: "a", kind: "join", title: "Link", inputs: ["b"], config: {} },
      { id: "b", kind: "join", title: "Link", inputs: ["a"], config: {} },
      { id: "a", kind: "agent", title: "Investigate", inputs: [], config: {} },
    ];
    expect(localIssues(plan)).toEqual(
      expect.arrayContaining([
        expect.stringContaining("must name a preceding step"),
        expect.stringContaining("duplicated"),
      ]),
    );
  });
  it.each([
    null,
    { version: "legacy" },
    { ...blankPlan(), steps: [{ id: "x" }] },
    { ...blankPlan(), disclosure: { recipients: [{}] } },
  ])("rejects unsafe editor shapes: %j", (value) => {
    expect(() => parsePlan(JSON.stringify(value))).toThrow();
  });
  it("reports authorization, expanded subject scope, cost, capability and release changes independently", () => {
    const before = blankPlan();
    const after = structuredClone(before);
    after.scope.subjects = ["employee-1", "employee-2"];
    after.agent.max_tokens *= 2;
    after.disclosure.recipients[0].allow_evidence = false;
    after.approval.roster = ["employee-1"];
    after.steps = [
      {
        id: "inspect",
        kind: "agent",
        title: "Inspect",
        inputs: [],
        config: {},
      },
    ];
    expect(semanticDiff(before, after).map((d) => d.category)).toEqual(
      expect.arrayContaining([
        "Evidence & subject scope",
        "Model & cost limits",
        "Recipient disclosure",
        "Authorization",
        "Method & capabilities",
      ]),
    );
    expect(before.scope.subjects).toEqual([]);
    expect(semanticDiff(before, before)).toEqual([]);
  });
});

describe("authenticated product API", () => {
  it("sends cookie auth, expected digest, real run modes and no caller identity", async () => {
    const fetch = vi
      .fn()
      .mockImplementation(() =>
        Promise.resolve(
          response({ plan: blankPlan(), digest: "next", run_id: "r-1" }),
        ),
      );
    vi.stubGlobal("fetch", fetch);
    const plan = blankPlan();
    plan.id = "my/plan";
    await api.savePlan(plan);
    await api.savePlan(plan, "old");
    await api.run(plan.id, "dataset-1", "rehearse");
    await api.run(plan.id, "dataset-1", "audit");
    await api.approve(plan.id, "next");
    expect(fetch.mock.calls.map((c) => c[0])).toEqual([
      "/api/workbench/plans",
      "/api/workbench/plans/my%2Fplan",
      "/api/workbench/runs",
      "/api/workbench/runs",
      "/api/workbench/plans/my%2Fplan/approve",
    ]);
    for (const [, init] of fetch.mock.calls) {
      expect(init.credentials).toBe("same-origin");
      expect(JSON.parse(init.body)).not.toHaveProperty("actor");
    }
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({
      plan,
      expected_digest: "old",
    });
    expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual({
      plan_id: plan.id,
      dataset_id: "dataset-1",
      mode: "rehearse",
    });
    expect(JSON.parse(fetch.mock.calls[3][1].body).mode).toBe("audit");
    expect(JSON.parse(fetch.mock.calls[4][1].body)).toEqual({ digest: "next" });
  });
  it("retains typed blocked/permission errors and never converts a failed check to success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() =>
        Promise.resolve(
          response(
            {
              error: {
                code: "consent_required",
                message: "All participants must approve.",
              },
            },
            403,
          ),
        ),
      ),
    );
    await expect(api.run("p", "d", "audit")).rejects.toMatchObject({
      status: 403,
      code: "consent_required",
      message: "All participants must approve.",
    });
    await expect(api.check(blankPlan())).rejects.toBeInstanceOf(ApiError);
  });
  it("treats an HTML login redirect or missing route as unavailable, not as a mock response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("<html>login</html>")),
    );
    await expect(request("/bootstrap")).rejects.toThrow("expected JSON");
  });
  it("uses the import, challenge, revoke and cancellation contract without extra authority", async () => {
    const fetch = vi
      .fn()
      .mockImplementation(() => Promise.resolve(response({})));
    vi.stubGlobal("fetch", fetch);
    const dataset = {
      id: "test",
      manifest: { coverage: {} },
      origin: "synthetic",
    };
    const challenge = {
      finding_id: "f-1",
      correction: "External review exists",
      evidence_ids: ["review-1"],
      requested_remedy: "Amend",
      recipients: ["auditor"],
    };
    await api.importDataset(dataset);
    await api.challenge("r/1", challenge);
    await api.revoke("p", "digest");
    await api.cancel("r/1");
    expect(fetch.mock.calls.map((c) => c[0])).toEqual([
      "/api/workbench/datasets",
      "/api/workbench/runs/r%2F1/challenge",
      "/api/workbench/plans/p/revoke",
      "/api/workbench/runs/r%2F1/cancel",
    ]);
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ dataset });
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual(challenge);
  });
});

describe("product run polling", () => {
  it("polls serially and stops at a terminal suppressed state", async () => {
    vi.useFakeTimers();
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(response({ status: "running" }))
      .mockResolvedValueOnce(response({ status: "suppressed" }));
    vi.stubGlobal("fetch", fetch);
    const onRun = vi.fn();
    const done = pollRun("own/run", onRun, new AbortController().signal, 10);
    await vi.advanceTimersByTimeAsync(0);
    expect(onRun).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(10);
    await done;
    await vi.advanceTimersByTimeAsync(60000);
    expect(onRun.mock.calls.map((c) => c[0].status)).toEqual([
      "running",
      "suppressed",
    ]);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch.mock.calls[0][0]).toBe("/api/workbench/runs/own%2Frun");
  });
  it("aborts between polls without another request or retained timer", async () => {
    vi.useFakeTimers();
    const fetch = vi
      .fn()
      .mockImplementation(() =>
        Promise.resolve(response({ status: "running" })),
      );
    vi.stubGlobal("fetch", fetch);
    const controller = new AbortController();
    const done = pollRun("r", vi.fn(), controller.signal, 10);
    await vi.advanceTimersByTimeAsync(0);
    controller.abort();
    await done;
    await vi.advanceTimersByTimeAsync(60000);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });
  it("reports a network failure instead of retrying invisibly or inventing a result", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    const callback = vi.fn();
    await expect(
      pollRun("r", callback, new AbortController().signal),
    ).rejects.toThrow("offline");
    expect(callback).not.toHaveBeenCalled();
  });
  it.each([
    "failed",
    "blocked",
    "cancelled",
    "suppressed",
    "completed",
    "released",
  ])("stops for %s", (status) => expect(runActive({ status })).toBe(false));
});

describe("result and recipient behavior", () => {
  it("mounts the full workbench without a backend response, keeping evaluation and real execution unavailable", () => {
    const html = renderToStaticMarkup(createElement(WorkbenchApp));
    for (const text of [
      "Question &amp; criteria",
      "Evidence &amp; boundaries",
      "Investigation method",
      "Checks &amp; counterevidence",
      "Disclosure agreement",
      "Approval &amp; operation",
      "Check plan",
      "Rehearse actual methods",
      "Request audit run",
      "Evaluate with DeepSeek",
      "unavailable",
      "Legacy builder",
    ])
      expect(html).toContain(text);
    expect(html).not.toContain("Simulation complete");
  });
  it("keeps no finding, contradicted, unsupported and execution failure distinct", () => {
    const labels = [
      "supported",
      "contradicted",
      "no_finding",
      "insufficient_evidence",
      "failed",
      "blocked",
      "suppressed",
    ].map(outcomeLabel);
    expect(new Set(labels).size).toBe(labels.length);
    expect(labels).toContain("Qualified negative finding");
    expect(labels).toContain("Suppressed · not zero");
    expect(results({ status: "failed" })).toContain(
      "A failure is not evidence of compliance",
    );
    expect(results({ status: "blocked" })).toContain(
      "This run establishes no finding",
    );
  });
  it("never renders withheld findings or presents a suppressed count as zero", () => {
    const html = results({
      status: "suppressed",
      result: { findings: [finding], status: "suppressed" },
    });
    expect(html).toContain("Suppression is not a zero count");
    expect(html).not.toContain(finding.claim);
    expect(html).not.toContain("0 released entries");
    expect(html).toContain("Results withheld");
  });
  it("shows supporting references, counterevidence, limitations and claim strength", () => {
    const html = results({
      run_id: "r-1",
      status: "completed",
      result: { findings: [finding], coverage: { missing_intervals: 1 } },
    });
    for (const text of [
      "event-support",
      "event-exception",
      "One collector interval is missing",
      "observed fact",
      "missing_intervals",
      "Challenge this finding",
    ])
      expect(html).toContain(text);
  });
  it("does not interpret an empty released array as a clean audit", () => {
    expect(
      results({ status: "completed", result: { findings: [] } }),
    ).toContain("This alone does not establish a negative finding");
    expect(results(null)).toContain(
      "No model result has been generated locally",
    );
  });
  it("shows recipient policy as an illustrative shape without requesting or projecting private results", () => {
    const plan = blankPlan();
    plan.disclosure.recipients[0].allow_evidence = false;
    plan.disclosure.recipients[0].allow_excerpts = false;
    const html = renderToStaticMarkup(
      createElement(RecipientPreview, { plan }),
    );
    expect(html).toContain("no private result is loaded");
    expect(html).toContain("not an audit result");
    expect(html).toContain("excluded");
    expect(html).toContain("suppressed, never reported as zero");
  });
});
