import { type Dispatch, useMemo } from "react";
import { blockPython, planPython } from "../plan/python";
import type { Issue } from "../plan/types";
import { topoOrder } from "../plan/validate";
import type { Action, State } from "../state/store";
import { BLOCK_META, kindOf } from "./nodeMeta";

interface Props {
  state: State;
  dispatch: Dispatch<Action>;
  /** Lint issues of the plan. */
  lint: Issue[];
}

/**
 * The plan as Python, read only: the wrapper (what connects to what) on top, then one fold
 * per block with its file, in the order the box runs them. The selected block's fold is open.
 */
export default function CodePane({ state, dispatch, lint }: Props) {
  const { plan, selected } = state;
  const blocks = useMemo(() => {
    let order: string[];
    try {
      order = topoOrder(plan);
    } catch {
      order = plan.nodes.map((n) => n.id);
    }
    return order.flatMap((id) => {
      const node = plan.nodes.find((n) => n.id === id);
      return node ? [{ node, text: blockPython(plan, node) }] : [];
    });
  }, [plan]);

  return (
    <div className="code">
      <div className="code-bar">
        <span className="code-title">The plan as Python</span>
        <span className="code-state">read only</span>
      </div>
      <div className="code-files">
        <div className="f">
          <span className="f-label">plan.py</span>
          <span className="f-hint">What connects to what.</span>
          <pre className="insp-py">{planPython(plan)}</pre>
        </div>
        {blocks.map(({ node, text }) => (
          <details key={node.id} className="f" open={node.id === selected}>
            <summary className="f-label">
              blocks/{node.id}.py <span className="muted">· {BLOCK_META[kindOf(node)].label}</span>
            </summary>
            <pre className="insp-py">{text}</pre>
          </details>
        ))}
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
