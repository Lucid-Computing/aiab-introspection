/** Problems in a well-formed plan. Shown on nodes and in the issue list; never blocking edits. */
import { LOG_SOURCES } from "./mockLogs";
import { acceptedTypes, accepts, inputType } from "./ports";
import { assignable, resolveReportPath } from "./report";
import type { Issue, Plan } from "./types";
import { upstream } from "./validate";

export function lintPlan(plan: Plan): Issue[] {
  const issues: Issue[] = [];
  const at = (i: number, ...rest: (string | number)[]) => ["nodes", i, ...rest];
  const err = (nodeId: string, path: (string | number)[], message: string) =>
    issues.push({ severity: "error", nodeId, path, message });
  const warn = (nodeId: string, path: (string | number)[], message: string) =>
    issues.push({ severity: "warning", nodeId, path, message });

  const reports = plan.nodes.filter((n) => n.type === "final_report");
  if (!plan.nodes.some((n) => n.type === "input_logs"))
    issues.push({ severity: "error", message: "The plan has no Input Logs node" });
  if (reports.length === 0)
    issues.push({ severity: "error", message: "The plan has no Final Report: nothing would be disclosed" });
  if (reports.length > 1)
    reports.forEach((r) => err(r.id, at(plan.nodes.indexOf(r)), "A plan has exactly one Final Report"));

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
        if (!LOG_SOURCES[n.config.source])
          err(
            n.id,
            at(i, "config", "source"),
            `${n.id}: unknown log source "${n.config.source}" (available: ${Object.keys(LOG_SOURCES).join(", ")})`,
          );
        if (!n.config.filter.kinds.length)
          err(
            n.id,
            at(i, "config", "filter", "kinds"),
            `${n.id}: no record kinds selected, so no records are admitted`,
          );
        break;
      case "classifier": {
        if (!n.inputs.items) err(n.id, at(i, "inputs"), `${n.id}: connect logs or items to classify`);
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
        if (!ins.length) err(n.id, at(i, "inputs"), `${n.id}: connect at least one input`);
        const out = n.config.output;
        if (out.type === "Value" && !Object.keys(out.fields).length)
          warn(
            n.id,
            at(i, "config", "output", "fields"),
            `${n.id}: output Value declares no fields, so a report cannot read it`,
          );
        if (n.type === "run_python" && !n.config.code.trim())
          warn(n.id, at(i, "config", "code"), `${n.id}: no Python code`);
        if (n.type === "run_llm") {
          if (!n.config.user_prompt.trim()) warn(n.id, at(i, "config", "user_prompt"), `${n.id}: empty user prompt`);
          for (const m of n.config.user_prompt.matchAll(/\{\{\s*([a-z][a-z0-9_]*)\s*\}\}/g))
            if (!ins.includes(m[1]))
              err(n.id, at(i, "config", "user_prompt"), `${n.id}: prompt uses {{${m[1]}}}, which is not an input`);
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
        if (!Object.keys(n.inputs).length) err(n.id, at(i, "inputs"), `${n.id}: connect the results to report`);
        if (!n.config.fields.length) err(n.id, at(i, "config", "fields"), `${n.id}: the report has no fields`);
        n.config.fields.forEach((f, k) => {
          const p = at(i, "config", "fields", k);
          const r = resolveReportPath(plan, n, f.from);
          if (!r.ok) err(n.id, [...p, "from"], `${n.id}.${f.name}: ${r.message}`);
          else if (!assignable(f.type, r.type))
            err(n.id, [...p, "type"], `${n.id}.${f.name}: declared ${f.type} but ${f.from} is ${r.type}`);
          if (f.type === "string" && !n.config.privacy.allow_free_text)
            err(
              n.id,
              [...p, "type"],
              `${n.id}.${f.name}: string fields can carry free text; set privacy.allow_free_text to release them`,
            );
        });
        const used = new Set(n.config.fields.map((f) => f.from.split(".")[0]));
        for (const name of Object.keys(n.inputs))
          if (!used.has(name)) warn(n.id, at(i, "inputs", name), `${n.id}: input "${name}" is not used by any field`);
        break;
      }
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
      if (!reach.has(n.id)) warn(n.id, at(i), `${n.id}: does not feed the Final Report`);
    });
  }
  return issues;
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
