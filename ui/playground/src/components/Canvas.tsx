import {
  applyNodeChanges,
  Background,
  type Connection,
  ControlButton,
  Controls,
  type EdgeChange,
  type NodeChange,
  ReactFlow,
  useReactFlow,
} from "@xyflow/react";
import { type Dispatch, type DragEvent, useCallback, useEffect, useMemo, useState } from "react";
import { type FlowNode, planToFlow } from "../plan/graph";
import { autoLayout } from "../plan/layout";
import { cannotAdd, connect, deleteNodes, disconnect } from "../plan/ops";
import type { Issue } from "../plan/types";
import type { Action, State } from "../state/store";
import NewBlock, { BLOCK_DRAG, OFFERED, placeBlock } from "./NewBlock";
import type { BlockKind } from "./nodeMeta";
import PlanNodeView from "./PlanNodeView";

const nodeTypes = {
  input_logs: PlanNodeView,
  filter_logs: PlanNodeView,
  combine_logs: PlanNodeView,
  classifier: PlanNodeView,
  run_python: PlanNodeView,
  run_llm: PlanNodeView,
  final_report: PlanNodeView,
};


interface Props {
  state: State;
  dispatch: Dispatch<Action>;
  issues: Issue[];
  bad: Set<string>;
}

export default function Canvas({ state, dispatch, issues, bad }: Props) {
  const { plan, layout, selected } = state;
  const flow = useReactFlow();
  const derived = useMemo(() => planToFlow(plan, layout, issues, bad), [plan, layout, issues, bad]);

  // React Flow keeps measured sizes on its node objects. Rebuild from the IR when it
  // changes, but carry those internals over so nodes do not vanish until re-measured.
  const [nodes, setNodes] = useState<FlowNode[]>(derived.nodes);
  useEffect(() => {
    setNodes((prev) => {
      const old = new Map(prev.map((n) => [n.id, n]));
      return derived.nodes.map((n) => {
        const p = old.get(n.id);
        const base = p ? { ...p, type: n.type, data: n.data, position: p.dragging ? p.position : n.position } : n;
        return { ...base, selected: n.id === selected };
      });
    });
  }, [derived.nodes, selected]);

  const [selectedEdges, setSelectedEdges] = useState<Set<string>>(new Set());
  const edges = useMemo(
    () =>
      derived.edges.map((e) => ({
        ...e,
        selected: selectedEdges.has(e.id),
        // Port names are in the hover text; spell the binding out only when it needs attention.
        label: selectedEdges.has(e.id) || e.data?.invalid ? e.label : undefined,
        // The edge takes the colour of the type it carries (styles.css .edge--<type>).
        className: e.data?.invalid ? "is-invalid" : `edge--${e.data?.type ?? "unknown"}`,
        labelBgPadding: [4, 2] as [number, number],
      })),
    [derived.edges, selectedEdges],
  );

  // Refit on request, and when the settings pane opens or closes: it halves the diagram.
  const paneOpen = Boolean(selected);
  useEffect(() => {
    const t = setTimeout(() => flow.fitView({ padding: 0.15, duration: 200, maxZoom: 1 }), 60);
    return () => clearTimeout(t);
  }, [state.fitSeq, paneOpen, flow]);

  // Selection is ours, and it happens on a click only (onNodeClick): pressing a block to
  // drag it must not open its settings. React Flow's own select changes are dropped.
  const onNodesChange = useCallback(
    (changes: NodeChange<FlowNode>[]) => {
      const kept = changes.filter((c) => c.type !== "select");
      setNodes((ns) => applyNodeChanges(kept, ns));
      const removed = kept.filter((c) => c.type === "remove").map((c) => c.id);
      if (removed.length) dispatch({ type: "update", f: (p) => deleteNodes(p, removed) });
      const moved: State["layout"] = {};
      for (const c of kept)
        if (c.type === "position" && c.position && c.dragging === false) moved[c.id] = c.position;
      if (Object.keys(moved).length) dispatch({ type: "moveNodes", layout: moved });
    },
    [dispatch],
  );

  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      setSelectedEdges((prev) => {
        const next = new Set(prev);
        for (const c of changes) if (c.type === "select") c.selected ? next.add(c.id) : next.delete(c.id);
        return next;
      });
      const removed = changes.filter((c) => c.type === "remove");
      if (!removed.length) return;
      const gone = removed.map((c) => derived.edges.find((x) => x.id === c.id)).filter((e) => e?.targetHandle) as {
        target: string;
        targetHandle: string;
      }[];
      dispatch({ type: "update", f: (p) => gone.reduce((q, e) => disconnect(q, e.target, e.targetHandle), p) });
    },
    [dispatch, derived.edges],
  );

  const onConnect = useCallback(
    (c: Connection) => {
      const r = connect(plan, c);
      if (!r.ok) dispatch({ type: "toast", text: r.message, kind: "error" });
      else {
        dispatch({ type: "setPlan", plan: r.plan });
        if (r.note) dispatch({ type: "toast", text: r.note });
      }
    },
    [dispatch, plan],
  );

  const onDrop = useCallback(
    (e: DragEvent) => {
      const t = e.dataTransfer.getData(BLOCK_DRAG) as BlockKind;
      if (!OFFERED.includes(t)) return;
      e.preventDefault();
      if (cannotAdd(plan, t)) return;
      placeBlock(plan, layout, dispatch, t, flow.screenToFlowPosition({ x: e.clientX, y: e.clientY }));
    },
    [plan, layout, dispatch, flow],
  );

  return (
    <div
      className="canvas"
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes(BLOCK_DRAG)) {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        }
      }}
      onDrop={onDrop}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        isValidConnection={(c) => c.source !== c.target}
        onNodeClick={(_, n) => dispatch({ type: "select", id: n.id })}
        selectNodesOnDrag={false}
        onPaneClick={() => dispatch({ type: "select", id: null })}
        deleteKeyCode={["Backspace", "Delete"]}
        fitView
        fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
        minZoom={0.2}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={24} size={1} color="rgba(30,31,34,0.10)" />
        <Controls showInteractive={false}>
          <ControlButton title="Auto-layout" onClick={() => dispatch({ type: "relayout", layout: autoLayout(plan) })}>
            ⇶
          </ControlButton>
        </Controls>
      </ReactFlow>
      <NewBlock plan={plan} layout={layout} dispatch={dispatch} />
    </div>
  );
}

