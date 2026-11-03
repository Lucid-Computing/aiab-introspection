import { useReactFlow } from "@xyflow/react";
import { type Dispatch, useEffect, useRef, useState } from "react";
import { READONLY } from "../agreement";
import { NODE_H, NODE_W } from "../plan/layout";
import { addNode } from "../plan/ops";
import { NODE_TYPES, type Plan } from "../plan/types";
import type { Action, State } from "../state/store";
import { NODE_META } from "./nodeMeta";

/** The "New block" button on the diagram: a menu of the block kinds; a click adds one near the centre. */
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
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  if (READONLY) return null;

  const add = (type: (typeof NODE_TYPES)[number]) => {
    const { plan: next, id } = addNode(plan, type);
    const el = root.current?.parentElement?.getBoundingClientRect();
    const c = el ? flow.screenToFlowPosition({ x: el.left + el.width / 2, y: el.top + el.height / 2 }) : { x: 0, y: 0 };
    const p = { x: c.x - NODE_W / 2, y: c.y - NODE_H / 2 };
    const taken = Object.values(layout);
    while (taken.some((q) => Math.abs(q.x - p.x) < NODE_W && Math.abs(q.y - p.y) < NODE_H)) p.y += NODE_H + 30;
    dispatch({ type: "setPlan", plan: next, layout: { ...layout, [id]: p }, select: id });
    setOpen(false);
  };

  return (
    <div className="newblock" ref={root}>
      <button className="btn btn--primary btn--sm" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        + New block
      </button>
      {open && (
        <div className="newblock-menu" role="menu">
          {NODE_TYPES.map((t) => (
            <button key={t} role="menuitem" className={`pal pal--${t}`} title={NODE_META[t].blurb} onClick={() => add(t)}>
              <span className="pn-glyph">{NODE_META[t].glyph}</span> {NODE_META[t].label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
