import { useReactFlow } from "@xyflow/react";
import type { Dispatch } from "react";
import { addNode } from "../plan/ops";
import { NODE_TYPES, type Plan } from "../plan/types";
import type { Action, State } from "../state/store";
import { DND_TYPE } from "./Canvas";
import { NODE_META } from "./nodeMeta";

export default function Palette({
  plan,
  layout,
  dispatch,
}: {
  plan: Plan;
  layout: State["layout"];
  dispatch: Dispatch<Action>;
}) {
  const flow = useReactFlow();
  const addAtCenter = (type: (typeof NODE_TYPES)[number]) => {
    const { plan: next, id } = addNode(plan, type);
    const el = document.querySelector(".canvas")?.getBoundingClientRect();
    const p = el ? flow.screenToFlowPosition({ x: el.left + el.width / 2, y: el.top + el.height / 2 }) : { x: 0, y: 0 };
    dispatch({ type: "setPlan", plan: next, layout: { ...layout, [id]: { x: p.x - 110, y: p.y - 40 } }, select: id });
  };
  return (
    <aside className="palette">
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
          title={`${NODE_META[t].blurb}\nDrag onto the canvas, or click to add.`}
        >
          <span className="pn-glyph">{NODE_META[t].glyph}</span> {NODE_META[t].label}
        </button>
      ))}
      <p className="palette-help">Drag right edge → left edge to connect. Backspace deletes.</p>
    </aside>
  );
}
