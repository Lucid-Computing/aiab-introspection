import { python } from "@codemirror/lang-python";
import { EditorView } from "@codemirror/view";
import CodeMirror from "@uiw/react-codemirror";
import { type Dispatch, useMemo } from "react";
import { planPython } from "../plan/python";
import type { Issue } from "../plan/types";
import type { Action, State } from "../state/store";

const extensions = [python(), EditorView.lineWrapping, EditorView.editable.of(false)];

interface Props {
  state: State;
  dispatch: Dispatch<Action>;
  /** Lint issues of the plan. */
  lint: Issue[];
}

/**
 * The wrapper as Python, read only: which block feeds which, in the order the box runs
 * them. A block's own code is in the Python fold of its settings pane.
 */
export default function CodePane({ state, dispatch, lint }: Props) {
  const { plan } = state;

  const text = useMemo(() => planPython(plan), [plan]);

  return (
    <div className="code">
      <div className="code-bar">
        <span className="code-title">plan.py</span>
        <span className="code-state">what connects to what · read only · each block's code is in its own Python fold</span>
      </div>
      <div className="code-editor">
        <CodeMirror
          value={text}
          height="100%"
          extensions={extensions}
          basicSetup={{ highlightActiveLine: false }}
        />
      </div>
      {lint.length > 0 && (
        <ul className="code-issues">
          {lint.map((i, k) => (
            <li key={k} className={i.severity}>
              <button className="linklike" onClick={() => i.nodeId && dispatch({ type: "select", id: i.nodeId })}>
                {i.message}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
