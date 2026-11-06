import { useDraft } from "./drafts";
import { useEffect, useRef, useState } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
} from "@xyflow/react";
import { JsonEditor, TextField } from "./Editor";
import { describeStep, parsePlan, pretty, semanticDiff } from "./model";
import type { Plan, Suggestion } from "./types";
import { api } from "./api";
import { JsonDetails } from "./Results";

export function GraphView({
  plan,
  selected,
  onSelect,
  onChange,
}: {
  plan: Plan;
  selected: string;
  onSelect: (id: string) => void;
  onChange: (p: Plan) => void;
}) {
  const nodes: Node[] = plan.steps.map((s, i) => ({
    id: s.id || `invalid-${i}`,
    position: { x: 70 + (i % 2) * 110, y: i * 125 },
    data: {
      label: (
        <>
          <small>{s.kind.toUpperCase()}</small>
          <br />
          <strong>{s.title}</strong>
          <br />
          <small>{s.id}</small>
        </>
      ),
    },
    selected: s.id === selected,
    style: {
      width: 250,
      background: "var(--paper-1)",
      border:
        s.id === selected
          ? "2px solid var(--amber-ink)"
          : "1px solid var(--rule-strong)",
      borderRadius: 0,
    },
  }));
  const edges: Edge[] = plan.steps.flatMap((s) =>
    s.inputs.map((input, i) => ({
      id: `${s.id}-${input}-${i}`,
      source: input,
      target: s.id,
      label: input,
      type: "smoothstep",
    })),
  );
  const step = plan.steps.find((s) => s.id === selected);
  return (
    <div className="wb-graph-view">
      <div className="wb-flow">
        <ReactFlowProvider>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodeClick={(_, node) => onSelect(node.id)}
            nodesDraggable={false}
            nodesConnectable={false}
            fitView
            minZoom={0.25}
            maxZoom={1.5}
          >
            <Background />
            <Controls showInteractive={false} />
          </ReactFlow>
        </ReactFlowProvider>
        {!nodes.length && (
          <p className="wb-graph-empty">
            Add investigation steps in the document to build the graph.
          </p>
        )}
      </div>
      <aside className="wb-graph-inspector">
        <h3>Linked clause</h3>
        {step ? (
          <>
            <p>{describeStep(step)}</p>
            <code>{step.id}</code>
            <TextField
              label="Step title"
              value={step.title}
              onChange={(title) =>
                onChange({
                  ...plan,
                  steps: plan.steps.map((s) =>
                    s === step ? { ...s, title } : s,
                  ),
                })
              }
            />
            <JsonEditor
              label="Executable settings"
              draftKey={`step:${step.id}`}
              value={step.config}
              onApply={(config) =>
                onChange({
                  ...plan,
                  steps: plan.steps.map((s) =>
                    s === step ? { ...s, config } : s,
                  ),
                })
              }
            />
          </>
        ) : (
          <p>
            Select a step to inspect its readable clause and executable
            settings.
          </p>
        )}
      </aside>
    </div>
  );
}
export function CodeView({
  plan,
  selected,
  onApply,
}: {
  plan: Plan;
  selected: string;
  onApply: (plan: Plan) => void;
}) {
  const { text: code, setText: setCode, reset, rebase, conflict } = useDraft("plan-code", pretty(plan));
  const [error, setError] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (!selected || !ref.current) return;
    const text = pretty(plan);
    const start = text.indexOf(`"id": ${JSON.stringify(selected)}`);
    if (start >= 0) {
      ref.current.focus();
      ref.current.setSelectionRange(start, start + selected.length + 8);
    }
  }, [selected, plan]);
  return (
    <div className="wb-code">
      <p>
        Exact executable agreement · unknown extension fields are preserved.
        Applying code changes invalidates prior check/approval status for this
        draft.
      </p>
      <label htmlFor="wb-plan-code">Plan JSON</label>
      <textarea
        id="wb-plan-code"
        ref={ref}
        rows={35}
        spellCheck={false}
        value={code}
        onChange={(e) => setCode(e.target.value)}
      />
      <div className="wb-actions">
        <button
          disabled={conflict || code === pretty(plan)}
          onClick={() => {
            try {
              onApply(parsePlan(code));
              reset();
              setError("");
            } catch (e) {
              setError((e as Error).message);
            }
          }}
        >
          Apply code to document
        </button>
        <button
          onClick={() => {
            reset();
            setError("");
          }}
        >
          Reset code buffer
        </button>
        {conflict && <p role="alert">Document changed beneath this code buffer. Reset it or <button onClick={rebase}>Keep code buffer against current document</button> before applying.</p>}
        {code !== pretty(plan) && (
          <small>Unapplied code is excluded from saving and execution.</small>
        )}
      </div>
      {error && (
        <p role="alert" className="wb-error">
          {error}
        </p>
      )}
    </div>
  );
}
export function Changes({
  baseline,
  plan,
  onChange,
  online = false,
}: {
  online?: boolean;
  baseline: Plan | null;
  plan: Plan;
  onChange: (p: Plan) => void;
}) {
  const [instruction, setInstruction] = useState("");
  const [pending, setPending] = useState(false);
  const [source, setSource] = useState("");
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null);
  const [text, setText] = useState("");
  const [proposal, setProposal] = useState<Plan | null>(null);
  const [error, setError] = useState("");
  const [accepted, setAccepted] = useState<Set<string>>(new Set());
  const changes = baseline ? semanticDiff(baseline, plan) : [];
  const proposed = proposal ? semanticDiff(plan, proposal) : [];
  // Method and evidence changes may depend on one another; accept this group atomically.
  const dependent = ["scope", "steps", "criteria", "question", "agent"];
  const stale = !!proposal && source !== pretty(plan);
  const toggle = (path: string) =>
    setAccepted((prev) => {
      const next = new Set(prev);
      const paths = dependent.includes(path)
        ? proposed.filter((d) => dependent.includes(d.path)).map((d) => d.path)
        : [path];
      const remove = next.has(path);
      paths.forEach((p) => (remove ? next.delete(p) : next.add(p)));
      return next;
    });
  return (
    <section className="wb-changes">
      <h2>Review the agreement difference</h2>
      <p>
        Changes are compared with the last saved agreement. A new executable
        digest requires fresh checks and authorization. Display layout and local
        review notes are outside the agreement.
      </p>
      {baseline ? (
        changes.length ? (
          changes.map((d) => (
            <article className="wb-card" key={d.path}>
              <h3>{d.category}</h3>
              <div className="wb-grid2">
                <div>
                  <small>Saved · {d.path}</small>
                  <pre>{pretty(d.before)}</pre>
                </div>
                <div>
                  <small>Draft · {d.path}</small>
                  <pre>{pretty(d.after)}</pre>
                </div>
              </div>
            </article>
          ))
        ) : (
          <p>No changes to the saved agreement.</p>
        )
      ) : (
        <p>This is an unsigned draft with no saved baseline.</p>
      )}
      <h3>Ask DeepSeek for an unsigned counterproposal</h3>
      <p>Use synthetic or public scope. Only the draft plan and your instruction are sent to the service and model; no dataset records are retrieved. Keep private data out of plan text and instructions.</p>
      <p>One verified completion · up to 24,000 input / 8,192 output tokens · 180 seconds. Provider failures are shown without fallback.</p>
      <TextField label="Authoring instruction" multiline value={instruction} onChange={setInstruction} />
      <button disabled={!online || pending || !instruction.trim() || instruction.length > 4000}
        onClick={async () => {
          const base = pretty(plan);
          setPending(true); setError(""); setProposal(null); setSuggestion(null); setAccepted(new Set());
          try {
            const result = await api.suggest(plan, instruction);
            const candidate = parsePlan(pretty(result.plan));
            if (candidate.id !== plan.id || candidate.version !== plan.version || !Array.isArray(result.changes)) throw new Error("Invalid counterproposal response.");
            setSource(base); setProposal(candidate); setSuggestion(result);
          } catch (e) { setError((e as Error).message); }
          finally { setPending(false); }
        }}>{pending ? "Requesting verified proposal…" : "Request unsigned proposal"}</button>
      <h3>Review a counterproposal</h3>
      <p>
        Paste a full proposed v1 plan from an authorized collaborator. This is a
        local editing tool. Evidence,
        question, criteria and method changes travel together.
      </p>
      <TextField
        label="Proposed agreement JSON"
        multiline
        value={text}
        onChange={setText}
      />
      <button
        onClick={() => {
          try {
            setProposal(parsePlan(text));
            setSource(pretty(plan));
            setSuggestion(null);
            setAccepted(new Set());
            setError("");
          } catch (e) {
            setError((e as Error).message);
          }
        }}
      >
        Compare proposal
      </button>
      {error && (
        <p role="alert" className="wb-error">
          {error}
        </p>
      )}
      {proposal && (
        <>
          {suggestion && <>
            <p>{suggestion.usage.test_model || suggestion.verification.test_model ? "TEST MODEL · server fixture, not live DeepSeek" : "Verified DeepSeek counterproposal"} · {suggestion.usage.total_tokens} tokens ({suggestion.usage.prompt_tokens} input / {suggestion.usage.completion_tokens} output)</p>
            <JsonDetails title="Verification document" value={suggestion.verification} />
            <JsonDetails title="Exact server JSON-pointer diff" value={suggestion.changes} />
          </>}
          {stale && <p role="alert" className="wb-error">The draft changed while this proposal was pending. Request or compare again before accepting.</p>}
          {!proposed.length && <p>No proposed changes.</p>}
          <div className="wb-notice">
            Review dependent changes together. Applying a proposal never signs
            or runs it; Check plan validates the resulting combination.
          </div>
          {proposed.map((d) => (
            <article className="wb-card" key={d.path}>
              <label className="wb-toggle">
                <input
                  type="checkbox"
                  disabled={stale}
                  checked={accepted.has(d.path)}
                  onChange={() => toggle(d.path)}
                />
                {d.category} · {d.path}
              </label>
              <div className="wb-grid2">
                <pre>{pretty(d.before)}</pre>
                <pre>{pretty(d.after)}</pre>
              </div>
            </article>
          ))}
          <button
            disabled={stale || !accepted.size}
            onClick={() => {
              if (stale) return;
              const next = { ...plan };
              accepted.forEach((k) => {
                if (Object.hasOwn(proposal, k)) next[k] = proposal[k];
                else delete next[k];
              });
              onChange(parsePlan(pretty(next)));
              setProposal(null);
              setAccepted(new Set());
            }}
          >
            Apply selected changes to draft
          </button>
        </>
      )}
    </section>
  );
}
