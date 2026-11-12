import { ReactFlowProvider } from "@xyflow/react";
import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { AGREEMENT, loadAgreement, READONLY, saveAgreement, type ServerIssue } from "./agreement";
import Canvas from "./components/Canvas";
import CodePane from "./components/CodePane";
import { Select } from "./components/fields";
import Inspector from "./components/Inspector";
import { badBindings, lintPlan } from "./plan/lint";
import { SAMPLE_SOURCES, samplePlan } from "./plan/samples";
import type { Issue } from "./plan/types";
import { exportJson, locateIssues, planToJson, jsonToPlan } from "./plan/json";
import { initialState, persist, reducer } from "./state/store";

/** What the server holds for the agreement being edited (agreement mode only). */
interface Server {
  digest: string;
  /** The JSON of the plan as last loaded or saved: the baseline for "unsaved changes". */
  savedCode: string;
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
  // The plan panel (title, description, all issues) opens from the status button.
  const [planOpen, setPlanOpen] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const { plan, codeOpen, selected } = state;
  const [server, setServer] = useState<Server>({
    digest: "",
    savedCode: "",
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
          savedCode: planToJson(s.plan),
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
    () => (state.codeIssues.length ? issues : locateIssues(state.code, issues)),
    // Only re-locate when the applied text changes, not on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [issues, state.codeIssues.length ? "" : state.code],
  );
  const nErr = issues.filter((i) => i.severity === "error").length;
  const nWarn = issues.length - nErr;

  // A new edit retires the server's objections to the previous attempt.
  const currentCode = planToJson(plan);
  useEffect(() => {
    if (server.refused.length) setServer((x) => ({ ...x, refused: [] }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentCode]);

  const dirty = Boolean(AGREEMENT) && !server.loading && !server.error && currentCode !== server.savedCode;
  useEffect(() => {
    if (selected) setPlanOpen(false);
  }, [selected]);

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
      setServer((x) => ({ ...x, saving: false, digest: r.digest, savedCode: currentCode, refused: [] }));
    } else {
      setServer((x) => ({ ...x, saving: false, refused: fromServer(r.issues) }));
      dispatch({ type: "toast", kind: "error", text: r.message });
      if (r.issues.length) dispatch({ type: "select", id: null });
    }
  };

  // Auto save: a valid change is written a moment after the last edit. A plan with errors
  // is not saved; the bar says so. A refused save waits for the next edit.
  const autoSave = Boolean(AGREEMENT) && server.editable && dirty && nErr === 0 && !server.saving && !server.refused.length;
  useEffect(() => {
    if (!autoSave) return;
    const t = setTimeout(save, 900);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoSave, currentCode]);

  const modified = (key: string) => planToJson(plan) !== planToJson(samplePlan(key));
  const loadSample = (key: string) => {
    if (
      modified(state.sample) &&
      !window.confirm("Replace the current plan with the sample? Export first to keep your changes.")
    )
      return;
    dispatch({ type: "loadPlan", plan: samplePlan(key), sample: key });
  };

  const download = () => {
    const blob = new Blob([exportJson(plan, state.layout)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${plan.plan_id}.plan.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const importFile = async (file: File) => {
    const r = jsonToPlan(await file.text(), { allowLayout: true });
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

  // Which half shows next to the diagram: a block's settings, or the plan as text.
  const pane = Boolean(selected) || planOpen;
  const saveNote = !AGREEMENT
    ? null
    : server.loading
      ? "loading…"
      : server.error
        ? server.error
        : !server.editable
          ? "read only"
          : server.saving
            ? "saving…"
            : dirty
              ? nErr
                ? "not saved · fix the errors"
                : "saving…"
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
            <Select
              className="bar-select"
              value={state.sample}
              onChange={(e) => loadSample(e.target.value)}
            >
              {SAMPLE_SOURCES.map((s) => (
                <option key={s.key} value={s.key}>
                  {s.label}
                </option>
              ))}
            </Select>
          )}
          <button
            className={`bar-status ${state.codeIssues.length ? "bad" : nErr ? "bad" : nWarn ? "warn" : "ok"}`}
            onClick={() => {
              dispatch({ type: "select", id: null });
              setPlanOpen(true);
            }}
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
          <button
            className={`bar-toggle${codeOpen ? " on" : ""}`}
            aria-pressed={codeOpen}
            title={codeOpen ? "Hide the plan as Python" : "Show the plan as Python"}
            onClick={() => dispatch({ type: "toggleCode" })}
          >
            <svg width="18" height="16" viewBox="0 0 18 16" aria-hidden="true">
              <rect x="0.75" y="0.75" width="16.5" height="14.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
              <rect x="10" y="2.5" width="5.75" height="11" fill="currentColor" />
            </svg>
          </button>
          <input
            ref={fileInput}
            type="file"
            accept=".json,application/json"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) importFile(f);
              e.target.value = "";
            }}
          />
        </header>

        {/* At most two halves: the diagram, and either a block's settings or the plan as text.
            With the text open, the selected block's settings sit as a card over the diagram. */}
        <main className={`work ${codeOpen || pane ? "work--two" : "work--one"}`}>
          <section className="graph">
            <Canvas state={state} dispatch={dispatch} issues={issues} bad={bad} />
            {codeOpen && pane && (
              <div className="card-over">
                <Inspector
                  plan={plan}
                  selected={selected}
                  issues={issues}
                  dispatch={dispatch}
                  onClose={() => setPlanOpen(false)}
                />
              </div>
            )}
          </section>
          {codeOpen ? (
            <CodePane state={state} dispatch={dispatch} lint={located} />
          ) : (
            pane && (
              <aside className="side">
                <Inspector
                  plan={plan}
                  selected={selected}
                  issues={issues}
                  dispatch={dispatch}
                  onClose={() => setPlanOpen(false)}
                />
              </aside>
            )
          )}
        </main>

        {toast && (
          <div key={toast.seq} className={`toast ${toast.kind}`} role="status" onClick={() => setToast(null)}>
            {toast.text}
          </div>
        )}
      </div>
    </ReactFlowProvider>
  );
}
