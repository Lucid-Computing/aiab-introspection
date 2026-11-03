import { Handle, type NodeProps, Position, useUpdateNodeInternals } from "@xyflow/react";
import { memo, useEffect } from "react";
import { type FlowNode, NEW_INPUT } from "../plan/graph";
import { accepts, INPUT_RULES } from "../plan/ports";
import type { PortType } from "../plan/types";
import { NODE_META, summary } from "./nodeMeta";

/**
 * One card per node. The plan flows top to bottom: inputs arrive at the top edge (one
 * handle per input, left to right in row order) and the output leaves at the bottom edge.
 * Below the title, an I/O footer: one row per input with the type it receives, then the
 * output row with its type as the most prominent element on the card, and the shape of one
 * value. The type badges on the two ends of an edge are what to compare: a red input badge
 * is a type the node does not accept. Row heights are fixed (styles.css), so layout.ts can
 * predict the card's size.
 */
function PlanNodeView({ id, data, selected }: NodeProps<FlowNode>) {
  const n = data.node;
  const meta = NODE_META[n.type];
  const rule = INPUT_RULES[n.type];
  const { inputs, output } = data.io;
  // A fresh block is unfinished (nothing connected yet); only a real mistake is an error.
  const errors = data.issues.filter((i) => i.severity === "error" && i.kind !== "incomplete").length;
  const todo = data.issues.find((i) => i.kind === "incomplete");
  // React Flow caches handle positions per node; re-measure when the set of handles changes.
  const updateInternals = useUpdateNodeInternals();
  const handleKey = `${inputs.map((i) => i.name).join(",")}|${output?.name ?? ""}`;
  useEffect(() => updateInternals(id), [id, handleKey, updateInternals]);

  return (
    <div
      className={`pn pn--${n.type}${selected ? " is-selected" : ""}${errors ? " has-error" : todo ? " is-unfinished" : ""}`}
      title={data.issues.map((i) => i.message).join("\n") || undefined}
    >
      {rule.kind !== "none" &&
        [
          ...inputs.map((i) => ({ id: i.name, fresh: false, title: `${i.name}: ${i.ref ? `reads ${i.ref}` : "not connected"}` })),
          ...(rule.kind === "dynamic" ? [{ id: NEW_INPUT, fresh: true, title: "Drop a connection here to add an input" }] : []),
        ].map((h, k, all) => (
          <Handle
            key={h.id}
            type="target"
            position={Position.Top}
            id={h.id}
            title={h.title}
            className={`pn-handle${h.fresh ? " pn-handle--new" : ""}`}
            style={{ left: `${((k + 1) / (all.length + 1)) * 100}%` }}
          />
        ))}
      <div className="pn-head">
        <span className="pn-kind">
          {meta.glyph} {meta.label}
        </span>
        {errors > 0 ? (
          <span className="pn-err">!</span>
        ) : (
          todo && <span className="pn-hint">{todo.message.replace(`${n.id}: `, "")}</span>
        )}
      </div>
      <div className="pn-title" title={n.title || n.id}>
        {n.title || n.id}
      </div>
      <div className="pn-line">{summary(n)[0]}</div>

      <div className="pn-io">
        <div className="pn-ins">
          {rule.kind === "none" && <div className="pn-row pn-row--none">in: none · starts from the log source</div>}
          {inputs.map((i) => {
            const bad = i.type !== null && !accepts(n.type, i.type);
            return (
              <div
                key={i.name}
                className={`pn-row pn-row--in${i.ref ? "" : " is-unbound"}${bad ? " is-bad" : ""}`}
                title={
                  i.ref
                    ? `${i.name} reads ${i.ref}${i.shape ? `\n${i.shape}` : ""}${bad ? `\nThis node does not accept ${i.type}` : ""}`
                    : `${i.name}: not connected`
                }
              >
                <span className="pn-port-name">{i.name}</span>
                {i.ref ? (
                  <TypeBadge type={i.type} bad={bad} />
                ) : (
                  <span className="pn-port-type is-unbound">not connected</span>
                )}
              </div>
            );
          })}
          {rule.kind === "dynamic" && (
            <div className="pn-row pn-row--new" title="Drop a connection on the top edge to add an input">
              <span className="pn-port-type">{inputs.length ? "+ input" : "+ connect an input"}</span>
            </div>
          )}
        </div>
        {output && (
          <div className={`pn-out${output.type === "Report" ? " pn-out--report" : ""}`}>
            <div className="pn-row pn-row--out">
              <span className="pn-port-name">{output.name}</span>
              <TypeBadge type={output.type} />
            </div>
            <div className="pn-shape" title={output.shape}>
              {output.shape}
            </div>
          </div>
        )}
      </div>
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

/** The port type, styled the same wherever it appears so two ends of an edge can be compared. */
export function TypeBadge({ type, bad = false }: { type: PortType | "Report" | null; bad?: boolean }) {
  return <span className={`tbadge tbadge--${type ?? "unknown"}${bad ? " is-bad" : ""}`}>{type ?? "?"}</span>;
}

export default memo(PlanNodeView);
