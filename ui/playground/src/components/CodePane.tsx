import { python } from "@codemirror/lang-python";
import { EditorView } from "@codemirror/view";
import CodeMirror from "@uiw/react-codemirror";
import { type Dispatch, useEffect, useMemo, useRef } from "react";
import { blockPython, planPython } from "../plan/python";
import type { Issue } from "../plan/types";
import { topoOrder } from "../plan/validate";
import type { Action, State } from "../state/store";

const extensions = [python(), EditorView.lineWrapping, EditorView.editable.of(false)];

interface Props {
  state: State;
  dispatch: Dispatch<Action>;
  /** Lint issues of the plan. */
  lint: Issue[];
}

/**
 * The plan as Python, read only: the wrapper (plan.py), then one file per block in the
 * order the box runs them. It is drawn from the plan; edit a block's settings and it follows.
 */
export default function CodePane({ state, dispatch, lint }: Props) {
  const view = useRef<EditorView | null>(null);
  const { plan, selected } = state;

  const { text, starts } = useMemo(() => {
    let order: string[];
    try {
      order = topoOrder(plan);
    } catch {
      order = plan.nodes.map((n) => n.id);
    }
    const parts = [planPython(plan)];
    const at: Record<string, number> = {};
    for (const id of order) {
      const node = plan.nodes.find((n) => n.id === id);
      if (!node) continue;
      at[id] = parts.join("\n\n").split("\n").length + 2;
      parts.push(blockPython(plan, node));
    }
    return { text: parts.join("\n\n"), starts: at };
  }, [plan]);

  // The selected block's file is scrolled to.
  useEffect(() => {
    const v = view.current;
    const line = selected ? starts[selected] : undefined;
    if (!v || !line || line > v.state.doc.lines) return;
    v.dispatch({ effects: EditorView.scrollIntoView(v.state.doc.line(line).from, { y: "start", yMargin: 8 }) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  return (
    <div className="code">
      <div className="code-bar">
        <span className="code-title">plan.py and blocks</span>
        <span className="code-state">read only, drawn from the plan</span>
      </div>
      <div className="code-editor">
        <CodeMirror
          value={text}
          height="100%"
          extensions={extensions}
          onCreateEditor={(v) => (view.current = v)}
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
