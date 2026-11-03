import { json as jsonLang } from "@codemirror/lang-json";
import { type Diagnostic, lintGutter, setDiagnostics } from "@codemirror/lint";
import { EditorView } from "@codemirror/view";
import CodeMirror from "@uiw/react-codemirror";
import { type Dispatch, useEffect, useRef } from "react";
import type { Issue } from "../plan/types";
import { linesOf, planToJson } from "../plan/json";
import type { Action, State } from "../state/store";

const extensions = [jsonLang(), lintGutter(), EditorView.lineWrapping];

interface Props {
  state: State;
  dispatch: Dispatch<Action>;
  /** Lint issues of the applied plan, located in the current text. */
  lint: Issue[];
}

export default function CodePane({ state, dispatch, lint }: Props) {
  const view = useRef<EditorView | null>(null);
  const { code, codeIssues } = state;
  const blocked = codeIssues.length > 0;
  const shown = blocked ? codeIssues : lint;

  // Parse shortly after typing stops.
  useEffect(() => {
    const t = setTimeout(() => dispatch({ type: "applyCode", text: code }), 350);
    return () => clearTimeout(t);
  }, [code, dispatch]);

  useEffect(() => {
    const v = view.current;
    if (!v) return;
    const doc = v.state.doc;
    const diags: Diagnostic[] = shown
      .filter((i) => i.line && i.line <= doc.lines)
      .map((i) => {
        const line = doc.line(i.line!);
        return { from: line.from, to: line.to, severity: i.severity, message: i.message };
      });
    v.dispatch(setDiagnostics(v.state, diags));
  }, [shown, code]);

  const jump = (line?: number) => {
    const v = view.current;
    if (!v || !line || line > v.state.doc.lines) return;
    const pos = v.state.doc.line(line).from;
    v.dispatch({ selection: { anchor: pos }, effects: EditorView.scrollIntoView(pos, { y: "center" }) });
    v.focus();
  };

  // The selected block's lines are selected in the text (without taking focus) and scrolled to.
  useEffect(() => {
    const v = view.current;
    if (!v || !state.selected || blocked) return;
    const idx = state.plan.nodes.findIndex((n) => n.id === state.selected);
    if (idx < 0) return;
    const r = linesOf(code, ["nodes", idx]);
    if (!r || r.to > v.state.doc.lines) return;
    const from = v.state.doc.line(r.from).from;
    const to = v.state.doc.line(r.to).to;
    v.dispatch({ selection: { anchor: from, head: to }, effects: EditorView.scrollIntoView(from, { y: "start", yMargin: 24 }) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.selected]);

  return (
    <div className="code">
      <div className="code-bar">
        <span className="code-title">plan.json</span>
        {blocked ? (
          <span className="code-state bad">Not applied — the graph shows the last valid plan</span>
        ) : (
          <span className="code-state ok">In sync with the graph</span>
        )}
        <button
          className="bar-link"
          title="Rewrite the text as the box saves it (keys sorted, every default written out)"
          disabled={blocked}
          onClick={() => dispatch({ type: "editCode", text: planToJson(state.plan) })}
        >
          Format
        </button>
        {blocked && (
          <button
            className="bar-link"
            title="Discard the invalid edits"
            onClick={() => dispatch({ type: "editCode", text: planToJson(state.plan) })}
          >
            Revert to last valid
          </button>
        )}
      </div>
      <div className="code-editor">
        <CodeMirror
          value={code}
          height="100%"
          extensions={extensions}
          onCreateEditor={(v) => (view.current = v)}
          onChange={(text) => dispatch({ type: "editCode", text })}
          basicSetup={{ highlightActiveLine: false }}
        />
      </div>
      {shown.length > 0 && (
        <ul className={`code-issues${blocked ? " blocked" : ""}`}>
          {shown.map((i, k) => (
            <li key={k} className={i.severity}>
              <button className="linklike" onClick={() => jump(i.line)}>
                {i.line ? <span className="mono">L{i.line}</span> : null} {i.message}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
