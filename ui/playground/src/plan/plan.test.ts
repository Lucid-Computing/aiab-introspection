import { describe, expect, it, vi } from "vitest";
import { flowToPlan, planToFlow } from "./graph";
import { completeLayout } from "./layout";
import { lintPlan, pythonIssues } from "./lint";
import { addNode, connect, deleteNodes, renameInput, renameNode, renameOutput } from "./ops";
import { blockPython } from "./python";
import { addQuery, isQuery, parseQuery, QUERY_OUTPUT, queryCode, queryLine } from "./query";
import { SAMPLE_SOURCES, deployedSample, samplePlan } from "./samples";
import deployedPlan from "../../../../plan/plan.json";
import { classificationShape, describeNode, describeOutput, reportShape } from "./shape";
import type { Plan } from "./types";
import { validatePlan } from "./validate";
import { exportJson, jsonToPlan, linesOf, planToJson } from "./json";

const samples = SAMPLE_SOURCES.map((s) => [s.key, samplePlan(s.key)] as const);
const simple = () => samplePlan("simple");
const clone = <T>(x: T): T => structuredClone(x);

function mustParse(text: string): Plan {
  const r = jsonToPlan(text);
  if (!r.ok) throw new Error(r.issues.map((i) => `${i.line}: ${i.message}`).join("\n"));
  return r.plan;
}

function errors(text: string) {
  const r = jsonToPlan(text);
  expect(r.ok).toBe(false);
  return r.ok ? [] : r.issues;
}

describe("samples", () => {
  it.each(samples)("%s is a valid plan with no lint errors", (_, plan) => {
    expect(lintPlan(plan).filter((i) => i.severity === "error")).toEqual([]);
  });

  it("the first sample is the deployed plan: whatever plan/plan.json holds, logs first, report last", () => {
    const deployed = samplePlan("deployed");
    expect(deployed.plan_id).toBe(deployedPlan.plan_id);
    expect(deployed.nodes[0].type).toBe("input_logs");
    expect(deployed.nodes[deployed.nodes.length - 1].type).toBe("final_report");
    expect(deployed.nodes.length).toBe(deployedPlan.nodes.length);
  });

  it("falls back to the simple sample when the baked deployed plan is not a plan", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const legacy = { plan_id: "legacy", classification: { problem: "?" }, schema: "aiab-plan/v1" };
    expect(deployedSample(legacy).plan_id).toBe(samplePlan("simple").plan_id);
    expect(warn).toHaveBeenCalledTimes(1);
    warn.mockRestore();
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

  it("rejects inputs on Log Source and unknown classifier inputs", () => {
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

describe("lint mirrors the box's rules", () => {
  it("refuses Python the box would refuse: imports, run() signature, reflective builtins", () => {
    expect(pythonIssues("import os\ndef run(records):\n    return []\n", ["records"])).toEqual([
      expect.stringContaining("import os is not allowed"),
    ]);
    expect(pythonIssues("def go(records):\n    return []\n", ["records"])).toEqual([
      expect.stringContaining("define run"),
    ]);
    expect(pythonIssues("def run(a, b):\n    return {}\n", ["records"])).toEqual([
      expect.stringContaining("does not match the inputs"),
    ]);
    expect(pythonIssues("def run(records):\n    return open('x')\n", ["records"])).toEqual([
      expect.stringContaining("open is not allowed"),
    ]);
    expect(pythonIssues("import re\nP = re.compile('x')\ndef run(**inputs):\n    return []\n", ["a", "b"])).toEqual([]);
  });

  it("a model step produces Items or a declared Value, nothing else", () => {
    const p = clone(samplePlan("multi-step"));
    const llm = p.nodes.find((n) => n.id === "summarise")!;
    if (llm.type !== "run_llm") throw new Error();
    llm.config.output = { name: "summaries", type: "LogRecords", fields: {} };
    expect(lintPlan(p).map((i) => i.message)).toContainEqual(expect.stringContaining("produces Items"));
    llm.config.output = { name: "summaries", type: "Value", fields: {} };
    expect(lintPlan(p).map((i) => i.message)).toContainEqual(expect.stringContaining("declare its fields"));
  });
});

describe("lint (soft issues)", () => {
  it("flags type-incompatible connections without rejecting the plan", () => {
    const text = planToJson(simple()).replace('"result": "physics.results"', '"result": "logs.records"');
    const plan = mustParse(text);
    const msgs = lintPlan(plan).map((i) => i.message);
    expect(msgs.some((m) => /report\.result: receives LogRecords/.test(m))).toBe(true);
  });

  it("checks report fields against upstream declarations", () => {
    const p = samplePlan("two-classifiers");
    const report = p.nodes.find((n) => n.type === "final_report")!;
    if (report.type !== "final_report") throw new Error();
    report.config.fields.push({ name: "missing", type: "number" });
    report.config.fields.push({ name: "wrong", type: "boolean", from: "sessions" });
    const msgs = lintPlan(p).map((i) => i.message);
    expect(msgs).toContain(
      'report.missing: "combine" declares no field "missing" (fields: biology_share, flagged, physics_share, sessions)',
    );
    expect(msgs).toContain("report.wrong: declared boolean but sessions is integer");
  });

  it("requires exactly one Final Report", () => {
    const p = deleteNodes(simple(), ["report"]);
    expect(lintPlan(p).some((i) => /no Final Report/.test(i.message))).toBe(true);
  });
});

describe("graph → code (serialisation)", () => {
  it("writes the plan as the box saves it: every key, defaults included, keys sorted", () => {
    const plan = samplePlan("two-classifiers");
    const text = planToJson(plan);
    const raw = JSON.parse(text);
    expect(raw).toEqual(JSON.parse(JSON.stringify(plan)));
    expect(Object.keys(raw)).toEqual([...Object.keys(raw)].sort());
    expect(Object.keys(raw.nodes[0])).toEqual([...Object.keys(raw.nodes[0])].sort());
    // Defaults are written out, so the text is the whole plan.
    expect(text).toContain('"temperature": 0');
    expect(text).toContain('"max_output_tokens": 512');
  });

  it("keeps layout out of the plan code", () => {
    const p = simple();
    expect(planToJson(p)).not.toContain("layout");
    const exported = exportJson(p, { logs: { x: 1, y: 2 } });
    const r = jsonToPlan(exported, { allowLayout: true });
    expect(r.ok && r.layout).toEqual({ logs: { x: 1, y: 2 } });
    expect(r.ok && r.plan).toEqual(p);
    // The code view itself does not accept a layout key.
    expect(jsonToPlan(exported).ok).toBe(false);
  });
});

describe("code → graph (parsing)", () => {
  it("adding a node and binding in JSON adds a graph node and edge", () => {
    const raw = JSON.parse(planToJson(simple()));
    raw.nodes.push({
      id: "tally",
      type: "run_python",
      inputs: { physics: "physics.results" },
      config: {
        code: "def run(physics):\n    return {'n': len(physics)}\n",
        output: { name: "totals", type: "Value", fields: { n: "integer" } },
      },
    });
    const plan = mustParse(JSON.stringify(raw, null, 2));
    const { nodes, edges } = planToFlow(plan, completeLayout(plan, {}));
    expect(nodes.map((n) => n.id)).toContain("tally");
    expect(edges.find((e) => e.target === "tally")).toMatchObject({
      source: "physics",
      sourceHandle: "results",
      targetHandle: "physics",
    });
  });

  it("deleting a node in JSON removes it from the graph", () => {
    const raw = JSON.parse(planToJson(samplePlan("two-classifiers")));
    raw.nodes = raw.nodes.filter((n: { id: string }) => n.id !== "biology");
    const combine = raw.nodes.find((n: { id: string }) => n.id === "combine");
    delete combine.inputs.biology;
    const plan = mustParse(JSON.stringify(raw, null, 2));
    expect(plan.nodes.map((n) => n.id)).toEqual(["logs", "physics", "combine", "report"]);
    expect(planToFlow(plan, {}).edges.map((e) => e.id)).not.toContain("combine.biology");
  });
});

describe("invalid JSON", () => {
  it("reports syntax errors with a line number", () => {
    const text = planToJson(simple()).replace('"id": "physics"', '"id": physics');
    const [e] = errors(text);
    expect(e.message).toMatch(/^JSON syntax/);
    expect(e.line).toBe(text.split("\n").findIndex((l) => l.includes('"id": physics')) + 1);
  });

  it("reports schema errors at the offending line", () => {
    const text = planToJson(simple()).replace('"threshold": 0.6', '"threshold": 7');
    const [e] = errors(text);
    const line = text.split("\n").findIndex((l) => l.includes('"threshold": 7')) + 1;
    expect(e.line).toBe(line);
    expect(e.message).toMatch(/config\.threshold/);
  });

  it("reports dangling references at the binding", () => {
    const text = planToJson(simple()).replace('"items": "logs.records"', '"items": "log.records"');
    const [e] = errors(text);
    expect(e.message).toContain('no node "log"');
    expect(e.line).toBe(text.split("\n").findIndex((l) => l.includes('"items": "log.records"')) + 1);
  });

  it("rejects a document that is not an object", () => {
    expect(errors("[1, 2]\n")[0].message).toMatch(/must be a JSON object/);
  });

  it("finds the lines of a block, to highlight it when selected", () => {
    const text = planToJson(simple());
    const r = linesOf(text, ["nodes", 1]);
    const lines = text.split("\n");
    expect(r && lines[r.from - 1].trim()).toBe("{");
    expect(r && lines.slice(r.from - 1, r.to).join("\n")).toContain('"id": "physics"');
    expect(r && lines[r.to - 1].trim()).toMatch(/^\}/);
  });
});

describe("round trips preserve semantics", () => {
  it.each(samples)("%s: IR → JSON → IR", (_, plan) => {
    expect(mustParse(planToJson(plan))).toEqual(plan);
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
    const g2 = planToFlow(mustParse(planToJson(fromGraph.plan)), layout);
    expect(g2.nodes).toEqual(g1.nodes);
    expect(g2.edges).toEqual(g1.edges);
  });

  it("is stable: JSON → IR → JSON gives the same text", () => {
    const text = planToJson(samplePlan("multi-step"));
    expect(planToJson(mustParse(text))).toBe(text);
  });

  it("keeps Python source and prompts byte for byte", () => {
    const p = samplePlan("multi-step");
    const back = mustParse(planToJson(p));
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
    const raw = connect(p, { source: "logs", sourceHandle: "records", target: "report", targetHandle: "result" });
    expect(raw.ok).toBe(false);
    expect(!raw.ok && raw.message).toMatch(/report discloses only aggregated results/);
    const toLogs = connect(p, { source: "aggregate", sourceHandle: "summary", target: "logs" });
    expect(toLogs.ok).toBe(false);
  });

  it("the Final Report is a sink with one input: connecting again replaces it", () => {
    const p = samplePlan("two-classifiers");
    const r = connect(p, { source: "physics", sourceHandle: "results", target: "report", targetHandle: "__new__" });
    if (!r.ok) throw new Error(r.message);
    expect(r.plan.nodes.find((n) => n.id === "report")!.inputs).toEqual({ result: "physics.results" });
    expect(r.note).toMatch(/was combine\.verdict/);
    const two = clone(p);
    two.nodes.find((n) => n.id === "report")!.inputs.extra = "physics.results";
    const v = validatePlan(two);
    expect(!v.ok && v.issues[0].message).toMatch(/unknown input "extra" \(allowed: result\)/);
  });

  it("deleting a node removes the bindings that read it", () => {
    const p = deleteNodes(samplePlan("two-classifiers"), ["biology"]);
    expect(validatePlan(p).ok).toBe(true);
    expect(p.nodes.find((n) => n.id === "combine")!.inputs).toEqual({ physics: "physics.results" });
  });

  it("renames keep references consistent", () => {
    const p = samplePlan("two-classifiers");
    const a = renameNode(p, "combine", "merge");
    expect(a.ok && a.plan.nodes.find((n) => n.id === "report")!.inputs).toEqual({ result: "merge.verdict" });
    const b = renameOutput(p, "combine", "outcome");
    expect(b.ok && b.plan.nodes.find((n) => n.id === "report")!.inputs).toEqual({ result: "combine.outcome" });
    const c = renameInput(p, "combine", "biology", "bio");
    expect(c.ok && c.plan.nodes.find((n) => n.id === "combine")!.inputs).toEqual({
      physics: "physics.results",
      bio: "biology.results",
    });
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

describe("port shapes (what the cards and inspector show)", () => {
  const node = (plan: Plan, id: string) => plan.nodes.find((n) => n.id === id)!;

  it("describes each node's output type and the shape of one value", () => {
    const plan = samplePlan("two-classifiers");
    expect(describeOutput(node(plan, "logs"))).toMatchObject({ name: "records", type: "LogRecords" });
    expect(describeOutput(node(plan, "physics"))).toMatchObject({
      name: "results",
      type: "Classifications",
      shape: "[{item_id, label: physics_research | other | abstain, score, evidence_ids}]",
      note: "2 labels",
    });
    // A Value's fields come out in key order, which is sorted, as in the saved file.
    expect(describeOutput(node(plan, "combine"))).toMatchObject({
      name: "verdict",
      type: "Value",
      shape: "{biology_share: number, flagged: boolean, physics_share: number, sessions: integer}",
      note: "4 fields",
    });
    expect(describeOutput(node(plan, "report"))).toMatchObject({ name: "releases", type: "Report" });
    expect(reportShape(node(plan, "report") as never)).toBe(
      "{flagged: boolean, physics_share: number, biology_share: number, sessions: integer}",
    );
  });

  it("lists inputs with the type and shape they receive, and marks unconnected ones", () => {
    const plan = samplePlan("two-classifiers");
    const combine = describeNode(plan, node(plan, "combine")).inputs;
    // Inputs are listed in key order, which is sorted, as in the saved file.
    expect(combine.map((i) => [i.name, i.ref, i.type])).toEqual([
      ["biology", "biology.results", "Classifications"],
      ["physics", "physics.results", "Classifications"],
    ]);
    expect(combine[1].shape).toContain("label: physics_research | other | abstain");

    const { plan: withClassifier, id } = addNode(plan, "classifier");
    expect(describeNode(withClassifier, node(withClassifier, id)).inputs).toEqual([
      { name: "items", ref: null, type: null, shape: null },
    ]);
  });

  it("the classifier's shape follows its labels, threshold and output contract", () => {
    const plan = clone(samplePlan("simple"));
    const c = node(plan, "physics");
    if (c.type !== "classifier") throw new Error("expected a classifier");
    c.config.threshold = undefined;
    c.config.output_contract = { score: false, evidence_ids: false };
    expect(classificationShape(c)).toBe("[{item_id, label: physics_research | other}]");
    c.config.labels = [];
    expect(classificationShape(c)).toBe("[{item_id, label: …}]");
  });

  it("a Value output with no fields says so", () => {
    const { plan, id } = addNode(samplePlan("simple"), "run_python");
    const n = node(plan, id);
    if (n.type !== "run_python") throw new Error("expected a Run Python block");
    n.config.output.fields = {};
    expect(describeOutput(n)).toMatchObject({ type: "Value", shape: "no fields declared", note: "0 fields" });
  });
});

describe("new blocks", () => {
  it("are titled like the others, counting up per kind", () => {
    const a = addNode(samplePlan("simple"), "run_python");
    const b = addNode(a.plan, "run_python");
    expect(a.plan.nodes.find((n) => n.id === a.id)!.title).toBe("Run Python");
    expect(b.plan.nodes.find((n) => n.id === b.id)!.title).toBe("Run Python 2");
  });

  it("start unfinished, not broken: the only errors are the missing connections", () => {
    const { plan } = addNode(addNode(samplePlan("simple"), "run_llm").plan, "run_python");
    const errors = lintPlan(plan).filter((i) => i.severity === "error");
    expect(errors.length).toBeGreaterThan(0);
    expect(errors.every((i) => i.kind === "incomplete")).toBe(true);
  });
});

describe("Query format block (a Run Python node the builder writes)", () => {
  const code = (c: string) => ({ ...addNode(simple(), "run_python").plan.nodes.at(-1)!, config: { code: c, output: QUERY_OUTPUT } }) as Plan["nodes"][number];

  it("round trips its two settings through the marker line", () => {
    for (const per of ["session", "record"] as const)
      for (const window of [1, 8, 32]) expect(parseQuery(code(queryCode({ per, window })))).toEqual({ per, window });
  });

  it("is recognised only by an exact first line", () => {
    const body = queryCode({ per: "session", window: 8 }).split("\n").slice(1).join("\n");
    for (const first of [
      "# query-format per=session window=0",
      "# query-format per=session window=08",
      "# query-format per=agent window=8",
      "# query-format per=session window=8 ",
      "#query-format per=session window=8",
    ])
      expect(parseQuery(code(`${first}\n${body}`))).toBeNull();
    expect(parseQuery(code(`\n${queryCode({ per: "session", window: 8 })}`))).toBeNull();
    expect(simple().nodes.some(isQuery)).toBe(false);
    expect(isQuery(addNode(simple(), "run_python").plan.nodes.at(-1)!)).toBe(false);
  });

  it("the generated code is admitted by the text check, for both settings", () => {
    for (const per of ["session", "record"] as const)
      expect(pythonIssues(queryCode({ per, window: 8 }), ["records"])).toEqual([]);
  });

  it("is added as query, then query_2, with an Items output and the default settings", () => {
    const a = addQuery(simple());
    const b = addQuery(a.plan);
    expect([a.id, b.id]).toEqual(["query", "query_2"]);
    const n = b.plan.nodes.find((x) => x.id === b.id)!;
    expect(n).toMatchObject({ type: "run_python", title: "Query format 2", config: { output: QUERY_OUTPUT } });
    expect(parseQuery(n)).toEqual({ per: "session", window: 8 });
    expect(queryLine(parseQuery(n)!)).toBe("per session · 8 records per question");
    expect(queryLine({ per: "record", window: 8 })).toBe("per record");
  });

  it("input_logs → query → classifier → report is a valid plan with no lint errors", () => {
    const p = simple();
    const logs = p.nodes.find((n) => n.type === "input_logs")!.id;
    const classifier = p.nodes.find((n) => n.type === "classifier")!.id;
    const { plan: added, id } = addQuery(p);
    const r1 = connect(added, { source: logs, target: id });
    if (!r1.ok) throw new Error(r1.message);
    const r2 = connect(r1.plan, { source: id, target: classifier });
    if (!r2.ok) throw new Error(r2.message);
    expect(r2.plan.nodes.find((n) => n.id === classifier)!.inputs).toEqual({ items: `${id}.items` });
    expect(validatePlan(r2.plan).ok).toBe(true);
    expect(lintPlan(r2.plan).filter((i) => i.severity === "error")).toEqual([]);
    expect(lintPlan(r2.plan).filter((i) => i.nodeId === id)).toEqual([]);
    // The plan file is unchanged in format: it survives the JSON round trip as a Run Python node.
    const back = mustParse(planToJson(r2.plan));
    expect(parseQuery(back.nodes.find((n) => n.id === id)!)).toEqual({ per: "session", window: 8 });
    const py = blockPython(r2.plan, r2.plan.nodes.find((n) => n.id === id)!);
    expect(py).toContain(`INPUT = {\n    "${logs}": "LogRecords",\n}`);
    expect(py).toContain('"type": "Items"');
    expect(py).toContain("# query-format per=session window=8\n");
  });

  it("lint says so when the code no longer matches its settings", () => {
    const { plan, id } = addQuery(simple());
    const n = plan.nodes.find((x) => x.id === id)!;
    if (n.type !== "run_python") throw new Error("expected a Run Python node");
    n.config.code = n.config.code.replace("WINDOW = 8", "WINDOW = 9");
    expect(lintPlan(plan).some((i) => i.nodeId === id && i.severity === "warning" && /settings generate/.test(i.message))).toBe(true);
  });
});

describe("the wrapper as Python", () => {
  it("shows only what connects to what", async () => {
    const { planPython } = await import("./python");
    const base = { version: "aiab-plan-ir/v0", plan_id: "t", title: "", limits: {}, nodes: [] } as unknown as Plan;
    let plan = addNode(validatePlanOrThrow(base), "input_logs").plan;
    const cls = addNode(plan, "classifier");
    plan = cls.plan;
    const rep = addNode(plan, "final_report");
    plan = rep.plan;
    const ids = plan.nodes.map((n) => n.id);
    const c1 = connect(plan, { source: ids[0], target: ids[1], targetHandle: "items" });
    if (!c1.ok) throw new Error(c1.message);
    const c2 = connect(c1.plan, { source: ids[1], target: ids[2], targetHandle: "result" });
    if (!c2.ok) throw new Error(c2.message);
    const text = planPython(c2.plan);
    expect(text).toContain(`from blocks import ${ids.join(", ")}`);
    expect(text).toContain(`records = ${ids[0]}()`);
    expect(text).toContain(`results = ${ids[1]}(items=records)`);
    expect(text).toContain(`${ids[2]}(result=results)`);
    expect(text).not.toContain("SETTINGS");
  });
});

function validatePlanOrThrow(p: Plan): Plan {
  const r = validatePlan(p);
  if (!r.ok) throw new Error(r.issues[0]?.message);
  return r.plan;
}
