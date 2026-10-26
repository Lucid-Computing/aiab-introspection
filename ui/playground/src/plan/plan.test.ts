import { describe, expect, it } from "vitest";
import { flowToPlan, planToFlow } from "./graph";
import { completeLayout } from "./layout";
import { lintPlan } from "./lint";
import { addNode, connect, deleteNodes, renameInput, renameNode, renameOutput } from "./ops";
import { SAMPLE_SOURCES, samplePlan } from "./samples";
import { simulate } from "./simulate";
import type { Plan } from "./types";
import { validatePlan } from "./validate";
import { exportYaml, planToYaml, yamlToPlan } from "./yaml";

const samples = SAMPLE_SOURCES.map((s) => [s.key, samplePlan(s.key)] as const);
const simple = () => samplePlan("simple");
const clone = <T>(x: T): T => structuredClone(x);

function mustParse(text: string): Plan {
  const r = yamlToPlan(text);
  if (!r.ok) throw new Error(r.issues.map((i) => `${i.line}: ${i.message}`).join("\n"));
  return r.plan;
}

function errors(text: string) {
  const r = yamlToPlan(text);
  expect(r.ok).toBe(false);
  return r.ok ? [] : r.issues;
}

describe("samples", () => {
  it.each(samples)("%s is a valid plan with no lint errors", (_, plan) => {
    expect(lintPlan(plan).filter((i) => i.severity === "error")).toEqual([]);
  });

  it("cover the three requested shapes", () => {
    const types = (p: Plan) => p.nodes.map((n) => n.type);
    expect(types(samplePlan("simple"))).toEqual(["input_logs", "classifier", "final_report"]);
    expect(types(samplePlan("two-classifiers"))).toEqual([
      "input_logs",
      "classifier",
      "classifier",
      "run_python",
      "final_report",
    ]);
    expect(types(samplePlan("multi-step"))).toEqual([
      "input_logs",
      "run_python",
      "run_llm",
      "classifier",
      "run_python",
      "final_report",
    ]);
  });
});

describe("Plan IR validation", () => {
  it("fills defaults so every node has a full config", () => {
    const p = samplePlan("two-classifiers");
    const physics = p.nodes.find((n) => n.id === "physics")!;
    expect(physics.type === "classifier" && physics.config.model).toEqual({
      id: "gpt-oss-safeguard-120b",
      temperature: 0,
      max_output_tokens: 512,
    });
  });

  it("rejects unknown node types and keys", () => {
    const raw = clone(simple()) as unknown as { nodes: Record<string, unknown>[] };
    raw.nodes[1].type = "run_sql";
    expect(validatePlan(raw).ok).toBe(false);
    const raw2 = clone(simple()) as unknown as { nodes: { config: Record<string, unknown> }[] };
    raw2.nodes[1].config.tempreature = 1;
    const r = validatePlan(raw2);
    expect(r.ok).toBe(false);
    expect(!r.ok && r.issues[0].message).toMatch(/nodes\[1\] \(physics\)\.config/);
  });

  it("rejects bad ids and duplicate ids", () => {
    const p = clone(simple());
    p.nodes[1].id = "Physics-1";
    expect(validatePlan(p).ok).toBe(false);
    const q = clone(simple());
    q.nodes[2].id = "physics";
    const r = validatePlan(q);
    expect(!r.ok && r.issues.map((i) => i.message).join()).toMatch(/Duplicate node id "physics"/);
  });

  it("rejects dangling references: missing node, missing output, self, report output", () => {
    const cases: [string, string][] = [
      ["nope.records", 'no node "nope"'],
      ["logs.rows", 'has no output "rows"'],
      ["physics.results", "cannot read its own output"],
      ["report.anything", "has no output"],
    ];
    for (const [ref, msg] of cases) {
      const p = clone(simple());
      p.nodes[1].inputs = { items: ref };
      const r = validatePlan(p);
      expect(r.ok, ref).toBe(false);
      expect(!r.ok && r.issues[0].message).toContain(msg);
    }
  });

  it("rejects inputs on Input Logs and unknown classifier inputs", () => {
    const p = clone(simple());
    p.nodes[0].inputs = { x: "physics.results" };
    expect(validatePlan(p).ok).toBe(false);
    const q = clone(simple());
    q.nodes[1].inputs = { records: "logs.records" };
    const r = validatePlan(q);
    expect(!r.ok && r.issues[0].message).toMatch(/unknown input "records"/);
  });

  it("detects cycles and names them", () => {
    const p = samplePlan("multi-step");
    const sessions = p.nodes.find((n) => n.id === "sessions")!;
    sessions.inputs.back = "disclosure.results";
    const r = validatePlan(p);
    expect(r.ok).toBe(false);
    expect(!r.ok && r.issues[0].message).toMatch(/^Cycle: .*disclosure.*sessions/);
  });
});

describe("lint (soft issues)", () => {
  it("flags type-incompatible connections without rejecting the plan", () => {
    const text = planToYaml(simple()).replace("physics: physics.results", "physics: logs.records");
    const plan = mustParse(text);
    const msgs = lintPlan(plan).map((i) => i.message);
    expect(msgs.some((m) => /report\.physics: receives LogRecords/.test(m))).toBe(true);
  });

  it("checks report fields against upstream declarations", () => {
    const p = samplePlan("two-classifiers");
    const report = p.nodes.find((n) => n.type === "final_report")!;
    if (report.type !== "final_report") throw new Error();
    report.config.fields.push({ name: "missing", type: "number", from: "verdict.nope" });
    report.config.fields.push({ name: "wrong", type: "boolean", from: "verdict.sessions" });
    const msgs = lintPlan(p).map((i) => i.message);
    expect(msgs).toContain('report.missing: "combine" declares no field "nope"');
    expect(msgs).toContain("report.wrong: declared boolean but verdict.sessions is integer");
  });

  it("requires exactly one Final Report", () => {
    const p = deleteNodes(simple(), ["report"]);
    expect(lintPlan(p).some((i) => /no Final Report/.test(i.message))).toBe(true);
  });
});

describe("graph → code (serialisation)", () => {
  it("writes Python and prompts as readable block text", () => {
    const text = planToYaml(samplePlan("multi-step"));
    expect(text).toContain("      code: |\n        import re\n");
    expect(text).toContain("      user_prompt: |\n        Session transcript:\n        {{session}}\n");
    expect(text).toContain("      session: sessions.sessions\n");
  });

  it("leaves out values equal to their defaults", () => {
    const text = planToYaml(samplePlan("two-classifiers"));
    expect(text).not.toMatch(/temperature|max_output_tokens|group_by|window|max_records|keywords|inputs: \{\}/);
    expect(text).toContain("      model: { id: gpt-oss-safeguard-120b }\n");
    expect(text).toContain("        - { name: flagged, type: boolean, from: verdict.flagged }\n");
    // Non-default values stay.
    expect(text).toContain("threshold: 0.6");
  });

  it("keeps layout out of the plan code", () => {
    const p = simple();
    expect(planToYaml(p)).not.toContain("layout");
    const exported = exportYaml(p, { logs: { x: 1, y: 2 } });
    const r = yamlToPlan(exported, { allowLayout: true });
    expect(r.ok && r.layout).toEqual({ logs: { x: 1, y: 2 } });
    expect(r.ok && r.plan).toEqual(p);
    // The code view itself does not accept a layout key.
    expect(yamlToPlan(exported).ok).toBe(false);
  });
});

describe("code → graph (parsing)", () => {
  it("adding a node and binding in YAML adds a graph node and edge", () => {
    const text =
      planToYaml(simple()) +
      `  - id: tally
    type: run_python
    inputs:
      physics: physics.results
    config:
      code: "def run(physics):\\n    return {'n': len(physics)}\\n"
      output: { name: totals, type: Value, fields: { n: integer } }
`;
    const plan = mustParse(text);
    const { nodes, edges } = planToFlow(plan, completeLayout(plan, {}));
    expect(nodes.map((n) => n.id)).toContain("tally");
    expect(edges.find((e) => e.target === "tally")).toMatchObject({
      source: "physics",
      sourceHandle: "results",
      targetHandle: "physics",
    });
  });

  it("deleting a node in YAML removes it from the graph", () => {
    const text = planToYaml(samplePlan("two-classifiers"))
      .replace(/ {2}- id: biology[\s\S]*?(?= {2}- id: combine)/, "")
      .replace("      biology: biology.results\n", "");
    const plan = mustParse(text);
    expect(plan.nodes.map((n) => n.id)).toEqual(["logs", "physics", "combine", "report"]);
    expect(planToFlow(plan, {}).edges.map((e) => e.id)).not.toContain("combine.biology");
  });
});

describe("invalid YAML", () => {
  it("reports syntax errors with a line number", () => {
    const text = planToYaml(simple()).replace("  - id: physics", "  - id: [physics");
    const [e] = errors(text);
    expect(e.message).toMatch(/^YAML syntax/);
    expect(e.line).toBeGreaterThan(1);
  });

  it("reports schema errors at the offending line", () => {
    const text = planToYaml(simple()).replace("threshold: 0.6", "threshold: 7");
    const [e] = errors(text);
    const line = text.split("\n").findIndex((l) => l.includes("threshold: 7")) + 1;
    expect(e.line).toBe(line);
    expect(e.message).toMatch(/config\.threshold/);
  });

  it("reports dangling references at the binding", () => {
    const text = planToYaml(simple()).replace("items: logs.records", "items: log.records");
    const [e] = errors(text);
    expect(e.message).toContain('no node "log"');
    expect(e.line).toBe(text.split("\n").findIndex((l) => l.includes("items: log.records")) + 1);
  });

  it("rejects a non-mapping document", () => {
    expect(errors("- just\n- a list\n")[0].message).toMatch(/must be a YAML mapping/);
  });
});

describe("round trips preserve semantics", () => {
  it.each(samples)("%s: IR → YAML → IR", (_, plan) => {
    expect(mustParse(planToYaml(plan))).toEqual(plan);
  });

  it.each(samples)("%s: IR → graph → IR", (_, plan) => {
    const { nodes, edges } = planToFlow(plan, completeLayout(plan, {}));
    const back = flowToPlan(plan, nodes, edges);
    expect(back.ok && back.plan).toEqual(plan);
  });

  it.each(samples)("%s: graph → code → graph", (_, plan) => {
    const layout = completeLayout(plan, {});
    const g1 = planToFlow(plan, layout);
    const fromGraph = flowToPlan(plan, g1.nodes, g1.edges);
    if (!fromGraph.ok) throw new Error("graph did not convert");
    const g2 = planToFlow(mustParse(planToYaml(fromGraph.plan)), layout);
    expect(g2.nodes).toEqual(g1.nodes);
    expect(g2.edges).toEqual(g1.edges);
  });

  it("is stable: YAML → IR → YAML gives the same text", () => {
    const text = planToYaml(samplePlan("multi-step"));
    expect(planToYaml(mustParse(text))).toBe(text);
  });

  it("keeps Python source and prompts byte for byte", () => {
    const p = samplePlan("multi-step");
    const back = mustParse(planToYaml(p));
    const py = (q: Plan) => q.nodes.find((n) => n.id === "sessions")!;
    const llm = (q: Plan) => q.nodes.find((n) => n.id === "summarise")!;
    expect(py(back)).toEqual(py(p));
    expect(llm(back)).toEqual(llm(p));
    const code =
      py(p).type === "run_python" ? (py(p) as Extract<Plan["nodes"][number], { type: "run_python" }>).config.code : "";
    expect(code).toContain('CUSTOMER_ID = re.compile(r"C-\\d+")');
  });
});

describe("graph edits", () => {
  it("connects one output to several nodes and several inputs into Run Python", () => {
    let p = simple();
    const added = addNode(p, "classifier");
    p = added.plan;
    const r1 = connect(p, { source: "logs", sourceHandle: "records", target: added.id, targetHandle: "items" });
    if (!r1.ok) throw new Error(r1.message);
    const py = addNode(r1.plan, "run_python");
    let q = py.plan;
    for (const src of ["physics", added.id]) {
      const r = connect(q, { source: src, sourceHandle: "results", target: py.id, targetHandle: "__new__" });
      if (!r.ok) throw new Error(r.message);
      q = r.plan;
    }
    expect(q.nodes.find((n) => n.id === py.id)!.inputs).toEqual({
      physics: "physics.results",
      [added.id]: `${added.id}.results`,
    });
    expect(q.nodes.filter((n) => Object.values(n.inputs).includes("logs.records"))).toHaveLength(2);
  });

  it("refuses cycles and incompatible types", () => {
    const p = samplePlan("multi-step");
    const cyc = connect(p, {
      source: "aggregate",
      sourceHandle: "summary",
      target: "sessions",
      targetHandle: "__new__",
    });
    expect(cyc).toEqual({ ok: false, message: "Connecting aggregate → sessions would create a cycle" });
    const raw = connect(p, { source: "logs", sourceHandle: "records", target: "report", targetHandle: "__new__" });
    expect(raw.ok).toBe(false);
    expect(!raw.ok && raw.message).toMatch(/report discloses only aggregated results/);
    const toLogs = connect(p, { source: "aggregate", sourceHandle: "summary", target: "logs" });
    expect(toLogs.ok).toBe(false);
  });

  it("deleting a node removes the bindings that read it", () => {
    const p = deleteNodes(samplePlan("two-classifiers"), ["biology"]);
    expect(validatePlan(p).ok).toBe(true);
    expect(p.nodes.find((n) => n.id === "combine")!.inputs).toEqual({ physics: "physics.results" });
  });

  it("renames keep references consistent", () => {
    const p = samplePlan("two-classifiers");
    const a = renameNode(p, "combine", "merge");
    expect(a.ok && a.plan.nodes.find((n) => n.id === "report")!.inputs).toEqual({ verdict: "merge.verdict" });
    const b = renameOutput(p, "combine", "outcome");
    expect(b.ok && b.plan.nodes.find((n) => n.id === "report")!.inputs).toEqual({ verdict: "combine.outcome" });
    const c = renameInput(p, "report", "verdict", "v");
    const rep = c.ok ? c.plan.nodes.find((n) => n.id === "report")! : null;
    expect(rep?.type === "final_report" && rep.config.fields.map((f) => f.from)).toEqual([
      "v.flagged",
      "v.physics_share",
      "v.biology_share",
      "v.sessions",
    ]);
    expect(renameNode(p, "combine", "physics").ok).toBe(false);
  });
});

describe("layout", () => {
  it("keeps existing positions and places new nodes", () => {
    const p = simple();
    const l = completeLayout(p, { logs: { x: 5, y: 7 } });
    expect(l.logs).toEqual({ x: 5, y: 7 });
    expect(Object.keys(l).sort()).toEqual(["logs", "physics", "report"]);
  });
});

describe("simulation", () => {
  it.each(samples)("%s produces a deterministic report with every declared field", (_, plan) => {
    const a = simulate(plan);
    expect(a).toEqual(simulate(plan));
    const report = plan.nodes.find((n) => n.type === "final_report")!;
    expect(Object.keys(a.report ?? {})).toEqual(
      report.type === "final_report" ? report.config.fields.map((f) => f.name) : [],
    );
    expect(Object.values(a.report ?? {})).not.toContain(null);
  });

  it("the simple plan flags the physics sessions in the synthetic logs", () => {
    const sim = simulate(simple());
    expect(sim.report).toEqual({ flagged: true });
    const physics = sim.outputs.physics;
    expect(
      physics.type === "Classifications" &&
        physics.value.filter((c) => c.label === "physics_research").map((c) => c.item_id),
    ).toEqual(["s01", "s03", "s07"]);
  });

  it("never claims Python ran", () => {
    expect(simulate(samplePlan("two-classifiers")).notes.join()).toMatch(/Python not executed/);
  });
});
