import { ReactFlowProvider } from "@xyflow/react";
import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { AGREEMENT, loadAgreement, READONLY, saveAgreement, type ServerIssue } from "./agreement";
import Canvas from "./components/Canvas";
import CodePane from "./components/CodePane";
import Inspector from "./components/Inspector";
import Palette from "./components/Palette";
import SimulateDialog from "./components/SimulateDialog";
import { badBindings, lintPlan } from "./plan/lint";
import { SAMPLE_SOURCES, samplePlan } from "./plan/samples";
import type { Issue } from "./plan/types";
import { exportYaml, locateIssues, planToYaml, yamlToPlan } from "./plan/yaml";
import { initialState, persist, reducer, type View } from "./state/store";

const VIEWS: [View, string][] = [
  ["graph", "Graph"],
  ["split", "Split"],
  ["code", "Code"],
];

/** What the server holds for the agreement being edited (agreement mode only). */
interface Server {
  digest: string;
  /** The YAML of the plan as last loaded or saved: the baseline for "unsaved changes". */
  savedYaml: string;
  editable: boolean;
  loading: boolean;
  saving: boolean;
  error: string | null;
  /** The box loader's reasons for refusing the last save; cleared by the next edit. */
  refused: Issue[];
}

const fromServer = (issues: ServerIssue[]): Issue[] =>
  issues.map((i) => ({ severity: "error", message: i.message, nodeId: i.node ?? undefined, path: i.path }));

export default function App() {
  const [state, dispatch] = useReducer(reducer, undefined, initialState);
  const [simOpen, setSimOpen] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const { plan, view } = state;
  const [server, setServer] = useState<Server>({
    digest: "",
    savedYaml: "",
    editable: false,
    loading: Boolean(AGREEMENT),
    saving: false,
    error: null,
    refused: [],
  });

  useEffect(() => {
    const t = setTimeout(() => persist(state), 250);
    return () => clearTimeout(t);
  }, [state]);

  // Agreement mode: the plan comes from the server, once, when the page opens.
  useEffect(() => {
    if (!AGREEMENT) return;
    let cancelled = false;
    loadAgreement(AGREEMENT)
      .then((s) => {
        if (cancelled) return;
        dispatch({ type: "loadPlan", plan: s.plan, sample: "deployed" });
        setServer((x) => ({
          ...x,
          digest: s.digest,
          savedYaml: planToYaml(s.plan),
          editable: s.editable && !READONLY,
          loading: false,
          refused: fromServer(s.issues),
        }));
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setServer((x) => ({ ...x, loading: false, error: e.message }));
        dispatch({ type: "toast", kind: "error", text: e.message });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const lint = useMemo(() => lintPlan(plan), [plan]);
  const issues = useMemo(() => [...server.refused, ...lint], [server.refused, lint]);
  const bad = useMemo(() => badBindings(plan), [plan]);
  const located = useMemo(
    () => (state.codeIssues.length ? issues : locateIssues(state.yaml, issues)),
    // Only re-locate when the applied text changes, not on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [issues, state.codeIssues.length ? "" : state.yaml],
  );
  const nErr = issues.filter((i) => i.severity === "error").length;
  const nWarn = issues.length - nErr;

  // A new edit retires the server's objections to the previous attempt.
  const currentYaml = planToYaml(plan);
  useEffect(() => {
    if (server.refused.length) setServer((x) => ({ ...x, refused: [] }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentYaml]);

  const dirty = Boolean(AGREEMENT) && !server.loading && !server.error && currentYaml !== server.savedYaml;
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const [toast, setToast] = useState(state.toast);
  useEffect(() => {
    if (!state.toast) return;
    setToast(state.toast);
    const t = setTimeout(() => setToast(null), 4500);
    return () => clearTimeout(t);
  }, [state.toast]);

  const save = async () => {
    if (!AGREEMENT || server.saving) return;
    setServer((x) => ({ ...x, saving: true }));
    const r = await saveAgreement(AGREEMENT, plan, server.digest);
    if (r.ok) {
      setServer((x) => ({ ...x, saving: false, digest: r.digest, savedYaml: currentYaml, refused: [] }));
      dispatch({ type: "toast", text: `Saved. New version ${r.digest.slice(7, 19)}…` });
    } else {
      setServer((x) => ({ ...x, saving: false, refused: fromServer(r.issues) }));
      dispatch({ type: "toast", kind: "error", text: r.message });
      if (r.issues.length) dispatch({ type: "select", id: null });
    }
  };

  const modified = (key: string) => planToYaml(plan) !== planToYaml(samplePlan(key));
  const loadSample = (key: string) => {
    if (
      modified(state.sample) &&
      !window.confirm("Replace the current plan with the sample? Export first to keep your changes.")
    )
      return;
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
      dispatch({
        type: "toast",
        kind: "error",
        text: `Import failed: ${r.issues[0].line ? `line ${r.issues[0].line}: ` : ""}${r.issues[0].message}`,
      });
      return;
    }
    // In agreement mode the plan keeps the agreement's name, whatever the file says.
    const imported = AGREEMENT ? { ...r.plan, plan_id: AGREEMENT } : r.plan;
    dispatch({ type: "loadPlan", plan: imported, layout: r.layout });
    dispatch({ type: "toast", text: `Imported ${file.name}${r.layout ? "" : " (laid out automatically)"}` });
  };

  const showGraph = view !== "code";
  const showCode = view !== "graph";
  const canSave = Boolean(AGREEMENT) && server.editable && dirty && nErr === 0 && !server.saving;
  const saveNote = !AGREEMENT
    ? null
    : server.loading
      ? "loading…"
      : server.error
        ? server.error
        : !server.editable
          ? "read only · press Edit this plan above to change it"
          : server.saving
            ? "saving…"
            : dirty
              ? nErr
                ? "unsaved · fix the errors first"
                : "unsaved changes"
              : `saved · ${server.digest.slice(7, 19)}…`;

  return (
    <ReactFlowProvider>
      <div className="app">
        <header className="bar">
          <span
            className="nav-wordmark"
            title={
              AGREEMENT
                ? "The plan of this agreement. Save writes it through the box's own loader."
                : "Playground: nothing here runs or is sent anywhere"
            }
          >
            {AGREEMENT ? `Plan builder · ${AGREEMENT}` : "Plan playground"}
          </span>
          {!AGREEMENT && (
            <select
              className="bar-select"
              value={state.sample}
              onChange={(e) => loadSample(e.target.value)}
              title="Sample plans"
            >
              {SAMPLE_SOURCES.map((s) => (
                <option key={s.key} value={s.key}>
                  {s.label}
                </option>
              ))}
            </select>
          )}
          <div className="seg" role="tablist">
            {VIEWS.map(([v, label]) => (
              <button
                key={v}
                role="tab"
                aria-selected={view === v}
                className={view === v ? "on" : ""}
                onClick={() => dispatch({ type: "view", view: v })}
              >
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
              : nErr
                ? `${nErr} error${nErr === 1 ? "" : "s"}`
                : nWarn
                  ? `${nWarn} warning${nWarn === 1 ? "" : "s"}`
                  : "Valid"}
          </button>
          {saveNote && (
            <span className={`bar-note${server.error ? " bad" : dirty ? "" : " ok"}`} role="status">
              {saveNote}
            </span>
          )}
          <div className="bar-spacer" />
          <button className="bar-link" onClick={() => fileInput.current?.click()}>
            Import
          </button>
          <button className="bar-link" onClick={download}>
            Export
          </button>
          {!AGREEMENT && (
            <button
              className="bar-link"
              onClick={() =>
                window.confirm("Reset to the selected sample? Your changes will be lost.") &&
                dispatch({ type: "loadPlan", plan: samplePlan(state.sample) })
              }
            >
              Reset
            </button>
          )}
          {AGREEMENT && server.editable && (
            <button
              className="bar-link"
              onClick={() =>
                dirty &&
                window.confirm("Discard the unsaved changes and go back to the saved plan?") &&
                (() => {
                  const r = yamlToPlan(server.savedYaml);
                  if (r.ok) dispatch({ type: "loadPlan", plan: r.plan });
                })()
              }
              disabled={!dirty}
            >
              Revert
            </button>
          )}
          <button className="btn btn--ghost btn--sm" onClick={() => setSimOpen(true)}>
            Simulate
          </button>
          {AGREEMENT && server.editable && (
            <button className="btn btn--primary btn--sm" onClick={save} disabled={!canSave} title="Save a new version">
              Save
            </button>
          )}
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
