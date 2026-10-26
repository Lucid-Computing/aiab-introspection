/**
 * Edits made from the graph and the inspector. Each takes a valid plan and returns a valid
 * plan (or a reason for refusing), so the IR never leaves the structural tier.
 */
import { accepts, INPUT_RULES, nodeById, outputPort, splitRef } from "./ports";
import type { NodeType, Plan, PlanNode } from "./types";
import { validatePlan, wouldCycle } from "./validate";
import { NEW_INPUT } from "./graph";

export type OpResult = { ok: true; plan: Plan; note?: string } | { ok: false; message: string };

export const DEFAULT_MODEL = { id: "gpt-oss-safeguard-120b", temperature: 0, max_output_tokens: 512 };

export function newNode(type: NodeType, id: string): PlanNode {
  switch (type) {
    case "input_logs":
      return {
        id,
        type,
        inputs: {},
        config: {
          source: "synthetic/research-agents",
          input_scope: { agents: [] },
          filter: { kinds: ["request", "response", "tool_call", "tool_result"], keywords: [] },
          max_records: 2000,
        },
      };
    case "classifier":
      return {
        id,
        type,
        inputs: {},
        config: {
          model: { ...DEFAULT_MODEL },
          group_by: "session",
          window: 8,
          instructions: "Decide whether this session shows the behaviour described below.",
          labels: [
            { name: "positive", description: "The behaviour is present", flag: true },
            { name: "negative", description: "The behaviour is absent", flag: false },
          ],
          threshold: 0.7,
          positive_examples: [],
          negative_examples: [],
          output_contract: { score: true, evidence_ids: true },
        },
      };
    case "run_python":
      return {
        id,
        type,
        inputs: {},
        config: {
          code: "def run(**inputs):\n    # Each declared input arrives as a keyword argument.\n    return {}\n",
          output: { name: "result", type: "Value", fields: {} },
        },
      };
    case "run_llm":
      return {
        id,
        type,
        inputs: {},
        config: {
          model: { ...DEFAULT_MODEL },
          system_prompt: "You are a careful analyst. Answer only in the requested JSON format.",
          user_prompt: "",
          output: { name: "result", type: "Value", fields: {} },
        },
      };
    case "final_report":
      return {
        id,
        type,
        inputs: {},
        config: {
          fields: [],
          privacy: { allow_free_text: false, min_count: 1, max_result_bytes: 1024 },
        },
      };
  }
}

const ID_BASE: Record<NodeType, string> = {
  input_logs: "logs",
  classifier: "classifier",
  run_python: "python",
  run_llm: "llm",
  final_report: "report",
};

export function freshId(plan: Plan, base: string): string {
  const ids = new Set(plan.nodes.map((n) => n.id));
  if (!ids.has(base)) return base;
  for (let k = 2; ; k++) if (!ids.has(`${base}_${k}`)) return `${base}_${k}`;
}

export function addNode(plan: Plan, type: NodeType): { plan: Plan; id: string } {
  const id = freshId(plan, ID_BASE[type]);
  return { plan: { ...plan, nodes: [...plan.nodes, newNode(type, id)] }, id };
}

export function deleteNodes(plan: Plan, ids: string[]): Plan {
  const gone = new Set(ids);
  return {
    ...plan,
    nodes: plan.nodes
      .filter((n) => !gone.has(n.id))
      .map((n) => ({
        ...n,
        inputs: Object.fromEntries(Object.entries(n.inputs).filter(([, r]) => !gone.has(splitRef(r)[0]))),
      })),
  };
}

export function disconnect(plan: Plan, target: string, input: string): Plan {
  return updateNode(plan, target, (n) => {
    const inputs = { ...n.inputs };
    delete inputs[input];
    return { ...n, inputs };
  });
}

export function updateNode(plan: Plan, id: string, f: (n: PlanNode) => PlanNode): Plan {
  return { ...plan, nodes: plan.nodes.map((n) => (n.id === id ? f(n) : n)) };
}

function freeInputName(node: PlanNode, base: string): string {
  if (!(base in node.inputs)) return base;
  for (let k = 2; ; k++) if (!(`${base}_${k}` in node.inputs)) return `${base}_${k}`;
}

/** Connect `source`'s output to `target`; refuses cycles and incompatible port types. */
export function connect(
  plan: Plan,
  c: { source: string; sourceHandle?: string | null; target: string; targetHandle?: string | null },
): OpResult {
  const byId = nodeById(plan);
  const src = byId.get(c.source);
  const tgt = byId.get(c.target);
  if (!src || !tgt) return { ok: false, message: "Unknown node" };
  const out = outputPort(src);
  if (!out) return { ok: false, message: "A Final Report has no output" };
  const rule = INPUT_RULES[tgt.type];
  if (rule.kind === "none") return { ok: false, message: "Input Logs is a starting node and takes no inputs" };
  if (!accepts(tgt.type, out.type))
    return {
      ok: false,
      message: `${tgt.id} cannot take ${out.type} from ${src.id}${
        tgt.type === "final_report" ? ": a report discloses only aggregated results" : ""
      }`,
    };
  if (wouldCycle(plan, src.id, tgt.id))
    return { ok: false, message: `Connecting ${src.id} → ${tgt.id} would create a cycle` };
  let name = c.targetHandle ?? NEW_INPUT;
  if (rule.kind === "fixed") name = rule.ports.includes(name) ? name : rule.ports[0];
  else if (name === NEW_INPUT) name = freeInputName(tgt, src.id);
  const ref = `${src.id}.${out.name}`;
  if (tgt.inputs[name] === ref) return { ok: true, plan };
  const replaced = tgt.inputs[name];
  const next = updateNode(plan, tgt.id, (n) => ({ ...n, inputs: { ...n.inputs, [name]: ref } }));
  return { ok: true, plan: next, note: replaced ? `${tgt.id}.${name} now reads ${ref} (was ${replaced})` : undefined };
}

/** Rename a node and every reference to it. */
export function renameNode(plan: Plan, from: string, to: string): OpResult {
  if (from === to) return { ok: true, plan };
  if (plan.nodes.some((n) => n.id === to)) return { ok: false, message: `"${to}" is already used` };
  const renamed: Plan = {
    ...plan,
    nodes: plan.nodes.map((n) => ({
      ...n,
      id: n.id === from ? to : n.id,
      inputs: Object.fromEntries(
        Object.entries(n.inputs).map(([k, r]) => {
          const [s, p] = splitRef(r);
          return [k, s === from ? `${to}.${p}` : r];
        }),
      ),
    })) as PlanNode[],
  };
  return checked(renamed);
}

/** Rename a Run Python / Run LLM output and the references that read it. */
export function renameOutput(plan: Plan, id: string, to: string): OpResult {
  const node = nodeById(plan).get(id);
  if (!node || (node.type !== "run_python" && node.type !== "run_llm"))
    return { ok: false, message: "No output to rename" };
  const old = `${id}.${node.config.output.name}`;
  const next: Plan = {
    ...plan,
    nodes: plan.nodes.map((n) => {
      const inputs = Object.fromEntries(Object.entries(n.inputs).map(([k, r]) => [k, r === old ? `${id}.${to}` : r]));
      if (n.id === id && (n.type === "run_python" || n.type === "run_llm"))
        return { ...n, inputs, config: { ...n.config, output: { ...n.config.output, name: to } } } as PlanNode;
      return { ...n, inputs };
    }),
  };
  return checked(next);
}

/** Rename one input of a node; a report's fields that read it follow. */
export function renameInput(plan: Plan, id: string, from: string, to: string): OpResult {
  const node = nodeById(plan).get(id);
  if (!node || !(from in node.inputs)) return { ok: false, message: `No input "${from}"` };
  if (from === to) return { ok: true, plan };
  if (to in node.inputs) return { ok: false, message: `Input "${to}" already exists` };
  const inputs = Object.fromEntries(Object.entries(node.inputs).map(([k, r]) => [k === from ? to : k, r]));
  const next = updateNode(plan, id, (n) => {
    if (n.type !== "final_report") return { ...n, inputs };
    const fields = n.config.fields.map((f) => {
      const [head, ...rest] = f.from.split(".");
      return head === from ? { ...f, from: [to, ...rest].join(".") } : f;
    });
    return { ...n, inputs, config: { ...n.config, fields } };
  });
  return checked(next);
}

function checked(plan: Plan): OpResult {
  const r = validatePlan(plan);
  return r.ok ? { ok: true, plan: r.plan } : { ok: false, message: r.issues[0].message };
}
