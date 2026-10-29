import {
  applyNodeChanges,
  Background,
  type Connection,
  ControlButton,
  Controls,
  type EdgeChange,
  MarkerType,
  type NodeChange,
  ReactFlow,
  useReactFlow,
} from "@xyflow/react";
import { type Dispatch, type DragEvent, useCallback, useEffect, useMemo, useState } from "react";
import { type FlowNode, planToFlow } from "../plan/graph";
import { autoLayout } from "../plan/layout";
import { addNode, connect, deleteNodes, disconnect } from "../plan/ops";
import type { Issue, NodeType } from "../plan/types";
import type { Action, State } from "../state/store";
import PlanNodeView from "./PlanNodeView";

const nodeTypes = {
  input_logs: PlanNodeView,
  classifier: PlanNodeView,
  run_python: PlanNodeView,
  run_llm: PlanNodeView,
  final_report: PlanNodeView,
};

export const DND_TYPE = "application/x-aiab-node";

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
        // Port names are on the nodes; spell the binding out only when it needs attention.
        label: selectedEdges.has(e.id) || e.data?.invalid ? e.label : undefined,
        className: e.data?.invalid ? "is-invalid" : undefined,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 14,
          height: 14,
          color: e.data?.invalid ? "#B4321C" : "#5F6165",
        },
        labelBgPadding: [4, 2] as [number, number],
      })),
    [derived.edges, selectedEdges],
  );

  useEffect(() => {
    const t = setTimeout(() => flow.fitView({ padding: 0.15, duration: 200, maxZoom: 1 }), 30);
    return () => clearTimeout(t);
  }, [state.fitSeq, flow]);

  const onNodesChange = useCallback(
    (changes: NodeChange<FlowNode>[]) => {
      setNodes((ns) => applyNodeChanges(changes, ns));
      const removed = changes.filter((c) => c.type === "remove").map((c) => c.id);
      if (removed.length) dispatch({ type: "update", f: (p) => deleteNodes(p, removed) });
      const moved: State["layout"] = {};
      for (const c of changes)
        if (c.type === "position" && c.position && c.dragging === false) moved[c.id] = c.position;
      if (Object.keys(moved).length) dispatch({ type: "moveNodes", layout: moved });
      for (const c of changes) if (c.type === "select" && c.selected) dispatch({ type: "select", id: c.id });
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
      const type = e.dataTransfer.getData(DND_TYPE) as NodeType;
      if (!type) return;
      e.preventDefault();
      const pos = flow.screenToFlowPosition({ x: e.clientX, y: e.clientY });
      const { plan: next, id } = addNode(plan, type);
      dispatch({
        type: "setPlan",
        plan: next,
        layout: { ...layout, [id]: { x: pos.x - 110, y: pos.y - 40 } },
        select: id,
      });
    },
    [dispatch, flow, layout, plan],
  );

  return (
    <div className="canvas" onDragOver={(e) => e.preventDefault()} onDrop={onDrop}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        isValidConnection={(c) => c.source !== c.target}
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
    </div>
  );
}
