import { useReactFlow } from "@xyflow/react";
import type { Dispatch } from "react";
import { READONLY } from "../agreement";
import { NODE_H, NODE_W } from "../plan/layout";
import { addCombine, addNode, cannotAdd } from "../plan/ops";
import { addQuery } from "../plan/query";
import type { Plan } from "../plan/types";
import type { Action, State } from "../state/store";
import { BLOCK_META, type BlockKind } from "./nodeMeta";

/** dataTransfer type for a block kind dragged from the rail onto the diagram. */
export const BLOCK_DRAG = "application/x-aiab-block";

/** The kinds shown at the top of the rail, in the order a plan usually runs them. */
export const MAIN: BlockKind[] = ["input_logs", "filter_logs", "and", "or", "final_report"];

/** The kinds kept under the collapsed "Deprecated" section at the bottom of the rail. */
export const DEPRECATED: BlockKind[] = ["query", "classifier", "run_llm", "run_python"];

/** Every kind a person adds by hand: the diagram accepts a drop of any of these. */
export const OFFERED: BlockKind[] = [...MAIN, ...DEPRECATED];

/** Add a block of `type` centred on `at` (flow coordinates), moved down until it overlaps no other block. */
export function placeBlock(
  plan: Plan,
  layout: State["layout"],
  dispatch: Dispatch<Action>,
  type: BlockKind,
  at: { x: number; y: number },
) {
  const { plan: next, id } =
    type === "query"
      ? addQuery(plan)
      : type === "and" || type === "or"
        ? addCombine(plan, type)
        : addNode(plan, type);
  const p = { x: at.x - NODE_W / 2, y: at.y - NODE_H / 2 };
  const taken = Object.values(layout);
  while (taken.some((q) => Math.abs(q.x - p.x) < NODE_W && Math.abs(q.y - p.y) < NODE_H)) p.y += NODE_H + 30;
  dispatch({ type: "setPlan", plan: next, layout: { ...layout, [id]: p }, select: id });
}

/** The block rail on the diagram: one button per kind. Click adds one near the centre; drag places it. */
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

  const addAtCentre = (type: BlockKind, el: HTMLElement) => {
    const r = el.closest(".canvas")?.getBoundingClientRect();
    const c = r ? flow.screenToFlowPosition({ x: r.left + r.width / 2, y: r.top + r.height / 2 }) : { x: 0, y: 0 };
    placeBlock(plan, layout, dispatch, type, c);
  };

  const item = (t: BlockKind) => {
    const blocked = cannotAdd(plan, t);
    return (
      <button
        key={t}
        className="newblock-item"
        title={blocked ?? "Click to add, or drag onto the diagram"}
        aria-label={`Add ${BLOCK_META[t].label}`}
        aria-disabled={blocked !== null}
        draggable={blocked === null}
        onDragStart={(e) => {
          e.dataTransfer.setData(BLOCK_DRAG, t);
          e.dataTransfer.effectAllowed = "copy";
        }}
        onClick={(e) => blocked === null && addAtCentre(t, e.currentTarget)}
      >
        <span className={`sym sym--${t}`} /> {BLOCK_META[t].label}
      </button>
    );
  };

  return (
    <div className="newblock" role="toolbar" aria-label="Add a block">
      {MAIN.map(item)}
      <details className="newblock-more">
        <summary>Deprecated</summary>
        <div className="newblock-more-items">{DEPRECATED.map(item)}</div>
      </details>
    </div>
  );
}
