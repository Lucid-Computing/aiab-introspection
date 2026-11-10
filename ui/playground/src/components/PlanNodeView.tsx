import { Handle, type NodeProps, Position, useUpdateNodeInternals } from "@xyflow/react";
import { memo, useEffect } from "react";
import { type FlowNode, NEW_INPUT } from "../plan/graph";
import { accepts, INPUT_RULES } from "../plan/ports";
import type { PortType } from "../plan/types";
import { Icon, KindTile, PortShape } from "./icons";
import { chips, labelDots, NODE_META } from "./nodeMeta";

/**
 * One block per node: the kind as an icon tile, the title, and a row of icon chips for the
 * settings worth seeing at a glance. The plan flows top to bottom: inputs are ports on the
 * top edge (left to right in row order), the output is a port on the bottom edge. A port's
 * shape and colour are its data type, and an edge takes the colour of the type it carries,
 * so a mismatch shows as two different shapes on one edge. Names, types and value shapes
 * are in the hover text. The block has a fixed size (layout.ts NODE_W x NODE_H).
 */
function PlanNodeView({ id, data, selected }: NodeProps<FlowNode>) {
  const n = data.node;
  const meta = NODE_META[n.type];
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
        type: i.ref ? i.type : null,
        bad,
        title: i.ref
          ? `${i.name} ← ${i.ref}: ${i.type ?? "?"}${i.shape ? `\n${i.shape}` : ""}${bad ? `\n${meta.label} does not accept ${i.type}` : ""}`
          : `${i.name}: not connected`,
      };
    }),
    ...(rule.kind === "dynamic"
      ? [{ id: NEW_INPUT, type: null, bad: false, title: "Drop a connection here to add an input" }]
      : []),
  ];

  const hover = [
    `${meta.label}: ${n.title || n.id}`,
    ...inputs.map((i) => `in  ${i.name} ← ${i.ref ? `${i.ref} (${i.type ?? "?"})` : "not connected"}`),
    ...(output ? [`out ${output.name}: ${output.type}`, `    ${output.shape}`] : []),
    ...data.issues.map((i) => `! ${i.message.replace(`${n.id}: `, "")}`),
  ].join("\n");

  const dots = labelDots(n);
  // Issue messages start with the node id ("report: …" or "report.result: …"); the block already says which.
  const short = (m: string) => m.replace(new RegExp(`^${n.id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}[.:]\\s*`), "");

  return (
    <div
      className={`pn pn--${n.type}${selected ? " is-selected" : ""}${errors.length ? " has-error" : todo ? " is-unfinished" : ""}`}
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
          >
            <PortShape type={h.type as PortType | null} />
          </Handle>
        ))}
      <KindTile type={n.type} />
      <div className="pn-body">
        <div className="pn-title">{n.title || n.id}</div>
        <div className="pn-chips">
          {errors.length > 0 ? (
            <span className="pn-chip pn-chip--err">
              <Icon name="warn" />
              {short(errors[0].message)}
            </span>
          ) : todo ? (
            <span className="pn-chip pn-chip--todo">{short(todo.message)}</span>
          ) : (
            <>
              {dots.length > 0 && (
                <span className="pn-chip" title={dots.map((d) => `${d.flag ? "⚑ " : ""}${d.name}${d.description ? `: ${d.description}` : ""}`).join("\n")}>
                  {dots.map((d) => (
                    <span key={d.name} className={`pn-dot${d.flag ? " is-flag" : ""}`} />
                  ))}
                </span>
              )}
              {chips(n).map((c, k) => (
                <span key={k} className="pn-chip" title={c.title}>
                  {c.icon && <Icon name={c.icon} />}
                  {c.text}
                </span>
              ))}
            </>
          )}
        </div>
      </div>
      {output && output.type !== "Report" && (
        <Handle
          type="source"
          position={Position.Bottom}
          id={output.name}
          className="pn-handle"
          title={`${output.name}: ${output.type}\n${output.shape}`}
        >
          <PortShape type={output.type} />
        </Handle>
      )}
    </div>
  );
}

/** The port type, styled the same wherever it appears; the shape matches the ports on the diagram. */
export function TypeBadge({ type, bad = false }: { type: PortType | "Report" | null; bad?: boolean }) {
  return (
    <span className={`tbadge tbadge--${type ?? "unknown"}${bad ? " is-bad" : ""}`}>
      <PortShape type={type} />
      {type ?? "?"}
    </span>
  );
}

export default memo(PlanNodeView);
