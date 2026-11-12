import { Handle, type NodeProps, Position, useUpdateNodeInternals } from "@xyflow/react";
import { memo, useEffect } from "react";
import { type FlowNode, NEW_INPUT } from "../plan/graph";
import { accepts, INPUT_RULES } from "../plan/ports";
import type { PortType } from "../plan/types";
import { BLOCK_META, kindOf, line } from "./nodeMeta";

/**
 * One block per node: the kind, the title, one line. The plan flows top to bottom: inputs
 * are ports on the top edge (left to right in row order), the output is a port on the bottom
 * edge. Names, types and value shapes are in the hover text. Fixed size (layout.ts).
 */
function PlanNodeView({ id, data, selected }: NodeProps<FlowNode>) {
  const n = data.node;
  const kind = kindOf(n);
  const meta = BLOCK_META[kind];
  const rule = INPUT_RULES[n.type];
  const { inputs, output } = data.io;
  // A fresh block is unfinished (nothing connected yet); only a real mistake is an error.
  const errors = data.issues.filter((i) => i.severity === "error" && i.kind !== "incomplete");
  const todo = data.issues.find((i) => i.kind === "incomplete");
  // React Flow caches handle positions per node; re-measure when the set of handles changes.
  const updateInternals = useUpdateNodeInternals();
  const handleKey = `${inputs.map((i) => i.name).join(",")}|${output?.name ?? ""}`;
  useEffect(() => updateInternals(id), [id, handleKey, updateInternals]);

  const ports = [
    ...inputs.map((i) => {
      const bad = i.type !== null && !accepts(n.type, i.type);
      return {
        id: i.name,
        bad,
        title: i.ref
          ? `${i.name} ← ${i.ref}: ${i.type ?? "?"}${i.shape ? `\n${i.shape}` : ""}${bad ? `\n${meta.label} does not accept ${i.type}` : ""}`
          : `${i.name}: not connected`,
      };
    }),
    ...(rule.kind === "dynamic" ? [{ id: NEW_INPUT, bad: false, title: "Drop a connection here to add an input" }] : []),
  ];

  const hover = [
    `${meta.label}: ${n.title || n.id}`,
    ...inputs.map((i) => `in  ${i.name} ← ${i.ref ? `${i.ref} (${i.type ?? "?"})` : "not connected"}`),
    ...(output ? [`out ${output.name}: ${output.type}`, `    ${output.shape}`] : []),
    ...data.issues.map((i) => `! ${i.message.replace(`${n.id}: `, "")}`),
  ].join("\n");

  // Issue messages start with the node id ("report: …" or "report.result: …"); the block already says which.
  const short = (m: string) => m.replace(new RegExp(`^${n.id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}[.:]\\s*`), "");
  const note = errors.length ? short(errors[0].message) : todo ? short(todo.message) : line(n);

  return (
    <div
      className={`pn${selected ? " is-selected" : ""}${errors.length ? " has-error" : todo ? " is-unfinished" : ""}`}
      title={hover}
    >
      {rule.kind !== "none" &&
        ports.map((h, k, all) => (
          <Handle
            key={h.id}
            type="target"
            position={Position.Top}
            id={h.id}
            title={h.title}
            className={`pn-handle${h.id === NEW_INPUT ? " pn-handle--new" : ""}${h.bad ? " is-bad" : ""}`}
            style={{ left: `${((k + 1) / (all.length + 1)) * 100}%` }}
          />
        ))}
      <div className="pn-kind">
        <span className={`sym sym--${kind}`} /> {meta.label}
      </div>
      <div className="pn-title">{n.title || n.id}</div>
      <div className={`pn-line${errors.length ? " is-bad" : todo ? " is-todo" : ""}`}>{note}</div>
      {output && output.type !== "Report" && (
        <Handle
          type="source"
          position={Position.Bottom}
          id={output.name}
          className="pn-handle"
          title={`${output.name}: ${output.type}\n${output.shape}`}
        />
      )}
    </div>
  );
}

/** The data type of a port, as plain text; red when the block does not accept it. */
export function TypeBadge({ type, bad = false }: { type: PortType | "Report" | null; bad?: boolean }) {
  return <span className={`tbadge${bad ? " is-bad" : ""}`}>{type ?? "?"}</span>;
}

export default memo(PlanNodeView);
