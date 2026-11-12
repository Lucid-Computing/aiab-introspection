/**
 * The Query format block: what one model question covers. It is not a node type of its own:
 * it is a Run Python node whose code starts with a marker line and whose body is generated
 * from the two settings in that line, so the plan format does not change. The body mirrors
 * `to_items` in static/aiab/baked/graph.py and passes the box's `check_code`.
 */
import { freshId } from "./ops";
import type { Output, Plan, PlanNode, RunPython } from "./types";

export interface Query {
  per: "session" | "record";
  /** Most records in one question (1 or more). Only used per session. */
  window: number;
}

export const QUERY_DEFAULT: Query = { per: "session", window: 8 };
export const QUERY_OUTPUT: Output = { name: "items", type: "Items", fields: {} };

const MARK = /^# query-format per=(session|record) window=([1-9]\d*)$/;

/** The settings of a Query format block, or null when the node is not one. */
export function parseQuery(node: PlanNode): Query | null {
  if (node.type !== "run_python") return null;
  const m = MARK.exec(node.config.code.split("\n", 1)[0]);
  if (!m) return null;
  return { per: m[1] as Query["per"], window: Number(m[2]) };
}

export function isQuery(node: PlanNode): node is RunPython {
  return parseQuery(node) !== null;
}

/** The Python the block runs: the marker line, the two settings, and the cutting. */
export function queryCode({ per, window }: Query): string {
  return `# query-format per=${per} window=${window}
# Written by the Query format block. Change its settings, not this text.
PER = "${per}"
WINDOW = ${window}


def run(**inputs):
    """Cut the records into the texts the model is asked about: one item per question."""
    records = list(inputs.values())[0]
    if PER == "record":
        return [{"id": r["id"], "text": r["content"], "record_ids": [r["id"]]} for r in records]
    sessions = {}
    for r in records:
        sessions.setdefault(r["session_id"], []).append(r)
    items = []
    for sid, rs in sessions.items():
        rs = sorted(rs, key=lambda r: (r["batch"], r["offset"]))
        for k in range(0, len(rs), WINDOW):
            chunk = rs[k : k + WINDOW]
            items.append({
                "id": sid if len(rs) <= WINDOW else f"{sid}#{k // WINDOW + 1}",
                "text": "\\n".join(f"{r['kind']}: {r['content']}" for r in chunk),
                "record_ids": [r["id"] for r in chunk],
            })
    return items
`;
}

/** The block's one line: "per session · 8 records per question". */
export function queryLine(q: Query): string {
  if (q.per === "record") return "per record";
  return `per session · ${q.window} record${q.window === 1 ? "" : "s"} per question`;
}

/** Add a Query format block, titled like the others ("Query format", then "Query format 2"). */
export function addQuery(plan: Plan): { plan: Plan; id: string } {
  const id = freshId(plan, "query");
  const same = plan.nodes.filter(isQuery).length;
  const node: RunPython = {
    id,
    type: "run_python",
    title: same ? `Query format ${same + 1}` : "Query format",
    inputs: {},
    config: { code: queryCode(QUERY_DEFAULT), output: { ...QUERY_OUTPUT } },
  };
  return { plan: { ...plan, nodes: [...plan.nodes, node] }, id };
}
