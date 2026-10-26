import { ReactFlowProvider } from "@xyflow/react";
import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import Canvas from "./components/Canvas";
import CodePane from "./components/CodePane";
import Inspector from "./components/Inspector";
import Palette from "./components/Palette";
import SimulateDialog from "./components/SimulateDialog";
import { autoLayout } from "./plan/layout";
import { badBindings, lintPlan } from "./plan/lint";
import { SAMPLE_SOURCES, samplePlan } from "./plan/samples";
import { exportYaml, locateIssues, planToYaml, yamlToPlan } from "./plan/yaml";
import { initialState, persist, reducer, type View } from "./state/store";

const VIEWS: [View, string][] = [
  ["graph", "Graph"],
  ["split", "Split"],
  ["code", "Code"],
];

export default function App() {
  const [state, dispatch] = useReducer(reducer, undefined, initialState);
  const [simOpen, setSimOpen] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const { plan, view } = state;

  useEffect(() => {
    const t = setTimeout(() => persist(state), 250);
    return () => clearTimeout(t);
  }, [state]);

  const issues = useMemo(() => lintPlan(plan), [plan]);
  const bad = useMemo(() => badBindings(plan), [plan]);
  const located = useMemo(
    () => (state.codeIssues.length ? issues : locateIssues(state.yaml, issues)),
    // Only re-locate when the applied text changes, not on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [issues, state.codeIssues.length ? "" : state.yaml],
  );
  const nErr = issues.filter((i) => i.severity === "error").length;
  const nWarn = issues.length - nErr;

  const [toast, setToast] = useState(state.toast);
  useEffect(() => {
    if (!state.toast) return;
    setToast(state.toast);
    const t = setTimeout(() => setToast(null), 4500);
    return () => clearTimeout(t);
  }, [state.toast]);

  const modified = (key: string) => planToYaml(plan) !== planToYaml(samplePlan(key));
  const loadSample = (key: string) => {
    if (modified(state.sample) && !window.confirm("Replace the current plan with the sample? Export first to keep your changes.")) return;
    dispatch({ type: "loadPlan", plan: samplePlan(key), sample: key });
  };

  const download = () => {
    const blob = new Blob([exportYaml(plan, state.layout)], { type: "text/yaml" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${plan.plan_id}.plan.yaml`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const importFile = async (file: File) => {
    const r = yamlToPlan(await file.text(), { allowLayout: true });
    if (!r.ok) {
      dispatch({ type: "toast", kind: "error", text: `Import failed: ${r.issues[0].line ? `line ${r.issues[0].line}: ` : ""}${r.issues[0].message}` });
      return;
    }
    dispatch({ type: "loadPlan", plan: r.plan, layout: r.layout });
    dispatch({ type: "toast", text: `Imported ${file.name}${r.layout ? "" : " (laid out automatically)"}` });
  };

  const showGraph = view !== "code";
  const showCode = view !== "graph";

  return (
    <ReactFlowProvider>
      <div className="app">
        <header className="bar">
          <div className="bar-brand">
            <span className="nav-wordmark">AIAB · Audit plan playground</span>
            <span className="badge badge--outline">prototype</span>
          </div>
          <select className="bar-select" value={state.sample} onChange={(e) => loadSample(e.target.value)} title="Sample plans">
            {SAMPLE_SOURCES.map((s) => (
              <option key={s.key} value={s.key}>
                {s.label}
              </option>
            ))}
          </select>
          <div className="seg" role="tablist">
            {VIEWS.map(([v, label]) => (
              <button key={v} role="tab" aria-selected={view === v} className={view === v ? "on" : ""} onClick={() => dispatch({ type: "view", view: v })}>
                {label}
              </button>
            ))}
          </div>
          <button
            className={`bar-status ${state.codeIssues.length ? "bad" : nErr ? "bad" : nWarn ? "warn" : "ok"}`}
            onClick={() => dispatch({ type: "select", id: null })}
            title="Show all issues"
          >
            {state.codeIssues.length
              ? `Code not applied (${state.codeIssues.length})`
              : [nErr && `${nErr} error${nErr === 1 ? "" : "s"}`, nWarn && `${nWarn} warning${nWarn === 1 ? "" : "s"}`]
                  .filter(Boolean)
                  .join(" · ") || "Valid plan"}
          </button>
          <div className="bar-spacer" />
          <button className="btn btn--ghost btn--sm" onClick={() => dispatch({ type: "relayout", layout: autoLayout(plan) })} disabled={!showGraph}>
            Auto-layout
          </button>
          <button className="btn btn--primary btn--sm" onClick={() => setSimOpen(true)}>
            Simulate
          </button>
          <button className="btn btn--ghost btn--sm" onClick={() => fileInput.current?.click()}>
            Import
          </button>
          <button className="btn btn--ghost btn--sm" onClick={download}>
            Export
          </button>
          <button
            className="btn btn--ghost btn--sm"
            onClick={() => window.confirm("Reset to the selected sample? Your changes will be lost.") && dispatch({ type: "loadPlan", plan: samplePlan(state.sample) })}
          >
            Reset
          </button>
          <input
            ref={fileInput}
            type="file"
            accept=".yaml,.yml,text/yaml"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) importFile(f);
              e.target.value = "";
            }}
          />
        </header>

        <main className={`work work--${view}${state.selected ? " has-sel" : ""}`}>
          {/* In split view the inspector takes the palette's column while a node is selected. */}
          {showGraph &&
            (view === "split" && state.selected ? (
              <aside className="side side--left">
                <Inspector plan={plan} selected={state.selected} issues={issues} dispatch={dispatch} />
              </aside>
            ) : (
              <Palette plan={plan} layout={state.layout} dispatch={dispatch} />
            ))}
          {showGraph && (
            <section className="graph">
              <Canvas state={state} dispatch={dispatch} issues={issues} bad={bad} />
            </section>
          )}
          {showCode && <CodePane state={state} dispatch={dispatch} lint={located} />}
          {view === "graph" && (
            <aside className="side">
              <Inspector plan={plan} selected={state.selected} issues={issues} dispatch={dispatch} />
            </aside>
          )}
        </main>

        {toast && (
          <div key={toast.seq} className={`toast ${toast.kind}`} role="status" onClick={() => setToast(null)}>
            {toast.text}
          </div>
        )}
        {simOpen && (
          <SimulateDialog
            plan={plan}
            blocked={issues.filter((i) => i.severity === "error").map((i) => i.message)}
            onClose={() => setSimOpen(false)}
          />
        )}
      </div>
    </ReactFlowProvider>
  );
}
