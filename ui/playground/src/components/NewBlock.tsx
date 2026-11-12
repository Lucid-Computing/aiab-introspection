import { useReactFlow } from "@xyflow/react";
import type { Dispatch } from "react";
import { READONLY } from "../agreement";
import { NODE_H, NODE_W } from "../plan/layout";
import { addNode } from "../plan/ops";
import { NODE_TYPES, type NodeType, type Plan } from "../plan/types";
import type { Action, State } from "../state/store";
import { KindTile } from "./icons";
import { NODE_META } from "./nodeMeta";

/** dataTransfer type for a block kind dragged from the rail onto the diagram. */
export const BLOCK_DRAG = "application/x-aiab-block";

/** Add a block of `type` centred on `at` (flow coordinates), moved down until it overlaps no other block. */
export function placeBlock(
  plan: Plan,
  layout: State["layout"],
  dispatch: Dispatch<Action>,
  type: NodeType,
  at: { x: number; y: number },
) {
  const { plan: next, id } = addNode(plan, type);
  const p = { x: at.x - NODE_W / 2, y: at.y - NODE_H / 2 };
  const taken = Object.values(layout);
  while (taken.some((q) => Math.abs(q.x - p.x) < NODE_W && Math.abs(q.y - p.y) < NODE_H)) p.y += NODE_H + 30;
  dispatch({ type: "setPlan", plan: next, layout: { ...layout, [id]: p }, select: id });
}

/** The block rail on the diagram: one icon per block kind. Click adds one near the centre; drag places it. */
export default function NewBlock({
  plan,
  layout,
  dispatch,
}: {
  plan: Plan;
  layout: State["layout"];
  dispatch: Dispatch<Action>;
}) {
  const flow = useReactFlow();
  if (READONLY) return null;

  const addAtCentre = (type: NodeType, el: HTMLElement) => {
    const r = el.closest(".canvas")?.getBoundingClientRect();
    const c = r ? flow.screenToFlowPosition({ x: r.left + r.width / 2, y: r.top + r.height / 2 }) : { x: 0, y: 0 };
    placeBlock(plan, layout, dispatch, type, c);
  };

  return (
    <div className="newblock" role="toolbar" aria-label="Add a block">
      {NODE_TYPES.map((t) => (
        <button
          key={t}
          className="newblock-item"
          title={`${NODE_META[t].label}\n${NODE_META[t].blurb}\nClick to add, or drag onto the diagram`}
          aria-label={`Add ${NODE_META[t].label}`}
          draggable
          onDragStart={(e) => {
            e.dataTransfer.setData(BLOCK_DRAG, t);
            e.dataTransfer.effectAllowed = "copy";
          }}
          onClick={(e) => addAtCentre(t, e.currentTarget)}
        >
          <KindTile type={t} small />
        </button>
      ))}
    </div>
  );
}
