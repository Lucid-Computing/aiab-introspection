/** Problems in a well-formed plan. Shown on nodes and in the issue list; never blocking edits. */
import { ruleIssue, ruleUnfinished } from "./filter";
import { LOG_SOURCES } from "./mockLogs";
import { acceptedTypes, accepts, inputType } from "./ports";
import { parseQuery, queryCode } from "./query";
import { assignable, fieldPath, resolveReportPath } from "./report";
import { isReport, type Issue, type Plan, type RecordsReport } from "./types";
import { upstream } from "./validate";

/** Whole modules a Run Python step may import; the box refuses anything else. */
/** The log sources a run can read: the ids of the agreement UI's sources (server/config.py). */
export const RUN_SOURCES = ["prod", "dev", "slack", "github", "plant"];

export const PYTHON_MODULES = ["re", "math", "collections", "itertools", "statistics", "json"];
const FORBIDDEN = /(?<![\w.])(eval|exec|compile|open|globals|locals|vars|getattr|setattr|delattr|breakpoint|input|__import__)\s*\(/;

/** What the box's code check would refuse, as far as text can tell (it parses the AST). */
export function pythonIssues(code: string, inputs: string[]): string[] {
  const out: string[] = [];
  if (!code.trim()) return out;
  for (const m of code.matchAll(/^\s*(?:import\s+([\w.]+)|from\s+([\w.]+)\s+import)/gm)) {
    const mod = (m[1] ?? m[2]).split(".")[0];
    if (!PYTHON_MODULES.includes(mod)) out.push(`import ${mod} is not allowed (allowed: ${PYTHON_MODULES.join(", ")})`);
  }
  const def = code.match(/^def\s+run\s*\(([^)]*)\)/m);
  if (!def) out.push("define run(...) taking the step's inputs");
  else if (!def[1].includes("**")) {
    const names = def[1]
      .split(",")
      .map((x) => x.trim().split(/[=:]/)[0].trim())
      .filter(Boolean);
    if ([...names].sort().join() !== [...inputs].sort().join())
      out.push(`run(${names.join(", ")}) does not match the inputs (${inputs.join(", ") || "none"})`);
  }
  const bad = code.match(FORBIDDEN);
  if (bad) out.push(`${bad[1]} is not allowed`);
  if (/\b__\w+/.test(code.replace(/__init__|__name__/g, ""))) out.push("dunder names are not allowed");
  if (/^\s*class\s/m.test(code)) out.push("classes are not allowed");
  return out;
}

export function lintPlan(plan: Plan): Issue[] {
  const issues: Issue[] = [];
  const at = (i: number, ...rest: (string | number)[]) => ["nodes", i, ...rest];
  const err = (nodeId: string, path: (string | number)[], message: string) =>
    issues.push({ severity: "error", nodeId, path, message });
  const warn = (nodeId: string, path: (string | number)[], message: string) =>
    issues.push({ severity: "warning", nodeId, path, message });
  const todo = (nodeId: string, path: (string | number)[], message: string) =>
    issues.push({ severity: "error", kind: "incomplete", nodeId, path, message });

  const reports = plan.nodes.filter(isReport);
  if (!plan.nodes.some((n) => n.type === "input_logs"))
    issues.push({ severity: "error", message: "The plan has no Log Source node" });
  if (reports.length === 0)
    issues.push({ severity: "error", message: "The plan has no report: nothing would be disclosed" });
  if (reports.length > 1)
    reports.forEach((r) =>
      err(r.id, at(plan.nodes.indexOf(r)), "A plan has exactly one report: Report or Report (old)"),
    );

  plan.nodes.forEach((n, i) => {
    for (const name of Object.keys(n.inputs)) {
      const t = inputType(plan, n, name);
      if (t && !accepts(n.type, t))
        err(
          n.id,
          at(i, "inputs", name),
          `${n.id}.${name}: receives ${t}, but this node accepts ${acceptedTypes(n.type).join(" or ")}` +
            (n.type === "final_report" ? ". Aggregate raw content first (Classifier or Run Python)." : ""),
        );
    }
    switch (n.type) {
      case "input_logs":
        if (!LOG_SOURCES[n.config.source] && !RUN_SOURCES.includes(n.config.source))
          warn(n.id, at(i, "config", "source"), `${n.id}: pick a log source; "${n.config.source}" is not one a run can read`);
        if (!n.config.filter.kinds.length)
          err(
            n.id,
            at(i, "config", "filter", "kinds"),
            `${n.id}: no record kinds selected, so no records are admitted`,
          );
        break;
      case "filter_logs": {
        if (!n.inputs.records) todo(n.id, at(i, "inputs"), `${n.id}: connect the records to filter`);
        const c = n.config;
        if (c.mode === "rules") {
          if (!c.rules.length) todo(n.id, at(i, "config", "rules"), `${n.id}: add a rule`);
          c.rules.forEach((rule, k) => {
            const why = ruleIssue(rule);
            if (why) (ruleUnfinished(why) ? todo : err)(n.id, at(i, "config", "rules", k), `${n.id}: rule ${k + 1}: ${why}`);
          });
        } else if (!c.llm.question.trim())
          todo(n.id, at(i, "config", "llm", "question"), `${n.id}: write the yes/no question`);
        break;
      }
      case "combine_logs": {
        const missing = ["a", "b"].filter((p) => !n.inputs[p]).map((p) => p.toUpperCase());
        if (missing.length) todo(n.id, at(i, "inputs"), `${n.id}: connect ${missing.join(" and ")}`);
        break;
      }
      case "classifier": {
        if (!n.inputs.items) todo(n.id, at(i, "inputs"), `${n.id}: connect logs or items to classify`);
        const names = n.config.labels.map((l) => l.name);
        if (names.length < 2) err(n.id, at(i, "config", "labels"), `${n.id}: a classifier needs at least two labels`);
        const dup = names.find((l, k) => names.indexOf(l) !== k);
        if (dup) err(n.id, at(i, "config", "labels"), `${n.id}: label "${dup}" appears twice`);
        if (names.includes("abstain"))
          err(n.id, at(i, "config", "labels"), `${n.id}: "abstain" is reserved for scores below the threshold`);
        if (!n.config.instructions.trim())
          warn(n.id, at(i, "config", "instructions"), `${n.id}: no classification instructions`);
        break;
      }
      case "run_python":
      case "run_llm": {
        const ins = Object.keys(n.inputs);
        if (!ins.length) todo(n.id, at(i, "inputs"), `${n.id}: connect at least one input`);
        const out = n.config.output;
        if (out.type === "Value" && !Object.keys(out.fields).length)
          warn(
            n.id,
            at(i, "config", "output", "fields"),
            `${n.id}: output Value declares no fields, so a report cannot read it`,
          );
        if (n.type === "run_python") {
          if (!n.config.code.trim()) warn(n.id, at(i, "config", "code"), `${n.id}: no Python code`);
          for (const why of pythonIssues(n.config.code, ins)) err(n.id, at(i, "config", "code"), `${n.id}: ${why}`);
          const q = parseQuery(n);
          if (q) {
            // The pane shows only the two settings, so the code must be what they generate.
            if (n.config.code !== queryCode(q))
              warn(n.id, at(i, "config", "code"), `${n.id}: the code is not what the Query format settings generate`);
            if (ins.length > 1 || ins.some((k) => (inputType(plan, n, k) ?? "LogRecords") !== "LogRecords"))
              err(n.id, at(i, "inputs"), `${n.id}: a Query format block reads one LogRecords input`);
            if (out.type !== "Items")
              err(n.id, at(i, "config", "output", "type"), `${n.id}: a Query format block outputs Items`);
          }
        }
        if (n.type === "run_llm") {
          if (!n.config.user_prompt.trim()) warn(n.id, at(i, "config", "user_prompt"), `${n.id}: empty user prompt`);
          for (const m of n.config.user_prompt.matchAll(/\{\{\s*([a-z][a-z0-9_]*)\s*\}\}/g))
            if (!ins.includes(m[1]))
              err(n.id, at(i, "config", "user_prompt"), `${n.id}: prompt uses {{${m[1]}}}, which is not an input`);
          if (out.type !== "Items" && out.type !== "Value")
            err(
              n.id,
              at(i, "config", "output", "type"),
              `${n.id}: a model call produces Items (one answer per item) or a Value`,
            );
          if (out.type === "Value" && !Object.keys(out.fields).length)
            err(
              n.id,
              at(i, "config", "output", "fields"),
              `${n.id}: a model call producing a Value must declare its fields`,
            );
          const lists = ins.filter((k) => ["LogRecords", "Items"].includes(inputType(plan, n, k) ?? ""));
          if (out.type === "Items" && lists.length !== 1)
            err(
              n.id,
              at(i, "config", "output", "type"),
              `${n.id}: an Items output runs once per item, so it needs exactly one LogRecords or Items input (has ${lists.length})`,
            );
        }
        break;
      }
      case "final_report": {
        if (!n.inputs.result) todo(n.id, at(i, "inputs"), `${n.id}: connect the one result to release`);
        if (!n.config.fields.length) todo(n.id, at(i, "config", "fields"), `${n.id}: the report has no fields`);
        n.config.fields.forEach((f, k) => {
          const p = at(i, "config", "fields", k);
          const path = fieldPath(f);
          const r = n.inputs.result ? resolveReportPath(plan, n, path) : null;
          if (r && !r.ok) err(n.id, [...p, f.from ? "from" : "name"], `${n.id}.${f.name}: ${r.message}`);
          else if (r?.ok && !assignable(f.type, r.type))
            err(n.id, [...p, "type"], `${n.id}.${f.name}: declared ${f.type} but ${path} is ${r.type}`);
          if (f.type === "string" && !n.config.privacy.allow_free_text)
            err(
              n.id,
              [...p, "type"],
              `${n.id}.${f.name}: string fields can carry free text; set privacy.allow_free_text to release them`,
            );
        });
        break;
      }
      case "records_report":
        for (const [why, where] of recordsReportIssues(n))
          (where[0] === "inputs" ? todo : err)(n.id, at(i, ...where), `${n.id}: ${why}`);
        break;
    }
  });

  // Nodes that cannot influence the report do no work anyone sees.
  if (reports.length === 1) {
    const ups = upstream(plan);
    const reach = new Set<string>();
    const todo = [reports[0].id];
    while (todo.length) {
      const id = todo.pop()!;
      if (reach.has(id)) continue;
      reach.add(id);
      todo.push(...(ups.get(id) ?? []));
    }
    plan.nodes.forEach((n, i) => {
      if (!reach.has(n.id)) warn(n.id, at(i), `${n.id}: does not feed the report`);
    });
  }
  return issues;
}

/** Why a Report cannot run, each with its path in the node: the box's records_report_issues. */
export function recordsReportIssues(n: RecordsReport): [string, string[]][] {
  const c = n.config;
  const out: [string, string[]][] = [];
  if (!n.inputs.records) out.push(["connect the records to report on", ["inputs"]]);
  if (c.include_records !== (c.records !== undefined))
    out.push([
      "include_records and the records settings go together",
      ["config", c.records ? "records" : "include_records"],
    ]);
  if (!c.metadata.length && !c.include_records) out.push(["choose something to report", ["config", "metadata"]]);
  if (c.metadata.includes("agent_ids") && !c.metadata.includes("agent_count"))
    out.push(["agent ids are reported with the agent count", ["config", "metadata"]]);
  return out;
}

/** Input bindings whose port types are incompatible: drawn as invalid edges. */
export function badBindings(plan: Plan): Set<string> {
  const bad = new Set<string>();
  for (const n of plan.nodes)
    for (const name of Object.keys(n.inputs)) {
      const t = inputType(plan, n, name);
      if (t && !accepts(n.type, t)) bad.add(`${n.id}.${name}`);
    }
  return bad;
}
