import { Handle, type NodeProps, Position, useUpdateNodeInternals } from "@xyflow/react";
import { memo, useEffect } from "react";
import { type FlowNode, NEW_INPUT } from "../plan/graph";
import { INPUT_RULES, outputPort } from "../plan/ports";
import { NODE_META, summary } from "./nodeMeta";

function PlanNodeView({ id, data, selected }: NodeProps<FlowNode>) {
  const n = data.node;
  const meta = NODE_META[n.type];
  const rule = INPUT_RULES[n.type];
  const out = outputPort(n);
  const errors = data.issues.filter((i) => i.severity === "error").length;
  const inputs = rule.kind === "fixed" ? rule.ports : Object.keys(n.inputs);
  // Port names matter only when there is more than one to tell apart.
  const named = rule.kind === "dynamic" && inputs.length > 1;
  // React Flow caches handle positions per node; re-measure when the set of handles changes.
  const updateInternals = useUpdateNodeInternals();
  const handleKey = `${inputs.join(",")}|${out?.name ?? ""}`;
  useEffect(() => updateInternals(id), [id, handleKey, updateInternals]);

  return (
    <div
      className={`pn pn--${n.type}${selected ? " is-selected" : ""}${errors ? " has-error" : ""}`}
      title={data.issues.map((i) => i.message).join("\n") || undefined}
    >
      <div className="pn-head">
        <span className="pn-kind">
          {meta.glyph} {meta.label}
        </span>
        {errors > 0 && <span className="pn-err">!</span>}
      </div>
      <div className="pn-title">{n.title || n.id}</div>
      <div className="pn-line">{detail(n)}</div>

      {named ? (
        <div className="pn-ins">
          {inputs.map((name) => (
            <div key={name} className="pn-port">
              <Handle type="target" position={Position.Left} id={name} className="pn-handle" />
              <span>{name}</span>
            </div>
          ))}
        </div>
      ) : (
        inputs.map((name) => (
          <Handle key={name} type="target" position={Position.Left} id={name} className="pn-handle" title={name} />
        ))
      )}
      {rule.kind === "dynamic" && (
        <Handle
          type="target"
          position={Position.Left}
          id={NEW_INPUT}
          className="pn-handle pn-handle--new"
          title="Drop here to add an input"
        />
      )}
      {out && (
        <Handle
          type="source"
          position={Position.Right}
          id={out.name}
          className="pn-handle"
          title={`${out.name}: ${out.type}`}
        />
      )}
    </div>
  );
}

/** One line: what the node does, in the fewest words. */
function detail(n: FlowNode["data"]["node"]): string {
  switch (n.type) {
    case "classifier":
      return n.config.labels.map((l) => (l.flag ? `${l.name}*` : l.name)).join(" · ");
    case "final_report":
      return n.config.fields.map((f) => f.name).join(", ") || "no fields yet";
    default:
      return summary(n)[0];
  }
}

export default memo(PlanNodeView);
