import { useReactFlow } from "@xyflow/react";
import type { Dispatch } from "react";
import { addNode } from "../plan/ops";
import { NODE_TYPES, type Plan } from "../plan/types";
import type { Action, State } from "../state/store";
import { DND_TYPE } from "./Canvas";
import { NODE_META } from "./nodeMeta";

export default function Palette({ plan, layout, dispatch }: { plan: Plan; layout: State["layout"]; dispatch: Dispatch<Action> }) {
  const flow = useReactFlow();
  const addAtCenter = (type: (typeof NODE_TYPES)[number]) => {
    const { plan: next, id } = addNode(plan, type);
    const el = document.querySelector(".canvas")?.getBoundingClientRect();
    const p = el
      ? flow.screenToFlowPosition({ x: el.left + el.width / 2, y: el.top + el.height / 2 })
      : { x: 0, y: 0 };
    dispatch({ type: "setPlan", plan: next, layout: { ...layout, [id]: { x: p.x - 130, y: p.y - 60 } }, select: id });
  };
  return (
    <aside className="palette">
      <div className="side-title">Blocks</div>
      {NODE_TYPES.map((t) => (
        <button
          key={t}
          className={`pal pal--${t}`}
          draggable
          onDragStart={(e) => {
            e.dataTransfer.setData(DND_TYPE, t);
            e.dataTransfer.effectAllowed = "move";
          }}
          onClick={() => addAtCenter(t)}
          title="Drag onto the canvas, or click to add"
        >
          <span className="pal-head">
            <span className="pn-glyph">{NODE_META[t].glyph}</span> {NODE_META[t].label}
          </span>
          <span className="pal-blurb">{NODE_META[t].blurb}</span>
        </button>
      ))}
      <div className="palette-help">
        <p>
          <strong>Connect:</strong> drag from an output (right edge) to an input (left edge). Drop on
          “+ input” to add a named input.
        </p>
        <p>
          <strong>Delete:</strong> select a node or edge and press Backspace.
        </p>
      </div>
    </aside>
  );
}
