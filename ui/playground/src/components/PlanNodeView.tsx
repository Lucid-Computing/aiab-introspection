import { Handle, type NodeProps, Position } from "@xyflow/react";
import { memo } from "react";
import { type FlowNode, NEW_INPUT } from "../plan/graph";
import { INPUT_RULES, outputPort } from "../plan/ports";
import { NODE_META, summary } from "./nodeMeta";

function PlanNodeView({ data, selected }: NodeProps<FlowNode>) {
  const n = data.node;
  const meta = NODE_META[n.type];
  const rule = INPUT_RULES[n.type];
  const out = outputPort(n);
  const errors = data.issues.filter((i) => i.severity === "error");
  const warnings = data.issues.filter((i) => i.severity === "warning");
  const inputs = rule.kind === "fixed" ? rule.ports : Object.keys(n.inputs);

  return (
    <div
      className={`pn pn--${n.type}${selected ? " is-selected" : ""}${errors.length ? " has-error" : ""}`}
      title={data.issues.map((i) => i.message).join("\n") || undefined}
    >
      <div className="pn-head">
        <span className="pn-glyph" aria-hidden>
          {meta.glyph}
        </span>
        <span className="pn-kind">{meta.label}</span>
        <span className="pn-id">{n.id}</span>
      </div>
      <div className="pn-body">
        <div className="pn-title">{n.title || meta.label}</div>
        {summary(n).map((line, k) => (
          <div key={k} className={k === 0 && n.type === "run_python" ? "pn-line mono" : "pn-line"}>
            {line}
          </div>
        ))}
        {n.type === "classifier" && (
          <div className="pn-chips">
            {n.config.labels.map((l) => (
              <span key={l.name} className={`pn-chip${l.flag ? " is-flag" : ""}`}>
                {l.name}
              </span>
            ))}
          </div>
        )}
        {n.type === "final_report" && (
          <ul className="pn-fields">
            {n.config.fields.map((f) => (
              <li key={f.name}>
                <span className="mono">{f.name}</span>: {f.type}
              </li>
            ))}
            {!n.config.fields.length && <li className="muted">no fields yet</li>}
          </ul>
        )}
      </div>

      {(inputs.length > 0 || rule.kind === "dynamic") && (
        <div className="pn-ports pn-ports--in">
          {inputs.map((name) => (
            <div key={name} className="pn-port">
              <Handle type="target" position={Position.Left} id={name} className="pn-handle" />
              <span>{name}</span>
            </div>
          ))}
          {rule.kind === "dynamic" && (
            <div className="pn-port pn-port--new" title="Drop a connection here to add an input">
              <Handle type="target" position={Position.Left} id={NEW_INPUT} className="pn-handle pn-handle--new" />
              <span>+ input</span>
            </div>
          )}
        </div>
      )}
      {out && (
        <div className="pn-ports pn-ports--out">
          <div className="pn-port">
            <span>
              {out.name} <span className="pn-type">{out.type}</span>
            </span>
            <Handle type="source" position={Position.Right} id={out.name} className="pn-handle" />
          </div>
        </div>
      )}
      {(errors.length > 0 || warnings.length > 0) && (
        <div className="pn-issues">
          {errors.length > 0 && <span className="pn-err">{errors.length} error{errors.length > 1 ? "s" : ""}</span>}
          {warnings.length > 0 && <span className="pn-warn">{warnings.length} warning{warnings.length > 1 ? "s" : ""}</span>}
        </div>
      )}
    </div>
  );
}

export default memo(PlanNodeView);
