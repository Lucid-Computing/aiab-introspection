import type { Dispatch } from "react";
import { connect, deleteNodes, disconnect, renameInput, renameOutput, updateNode } from "../plan/ops";
import { accepts, INPUT_RULES, inputType, outputPort } from "../plan/ports";
import { CLASSIFICATION_PATHS, classifierLabels, fieldPath, reportSource } from "../plan/report";
import { blockPython, planPython } from "../plan/python";
import { describeNode } from "../plan/shape";
import {
  type Classifier,
  FIELD_TYPES,
  type FieldType,
  type FinalReport,
  type InputLogs,
  type Issue,
  type Output,
  PORT_TYPES,
  type Plan,
  type PlanNode,
  RECORD_KINDS,
  type RunLlm,
  type RunPython,
} from "../plan/types";
import { wouldCycle } from "../plan/validate";
import type { Action } from "../state/store";
import { CommitInput, Field, LinesInput, NumberInput, PythonEditor } from "./fields";
import { NODE_META, SOURCE_OPTIONS, sourceLabel } from "./nodeMeta";
import { TypeBadge } from "./PlanNodeView";

interface Props {
  plan: Plan;
  selected: string | null;
  issues: Issue[];
  dispatch: Dispatch<Action>;
  /** Closes the plan panel (the one shown when no block is selected). */
  onClose?: () => void;
}

export default function Inspector({ plan, selected, issues, dispatch, onClose }: Props) {
  const node = plan.nodes.find((n) => n.id === selected);
  if (!node) return <PlanPanel plan={plan} issues={issues} dispatch={dispatch} onClose={onClose} />;

  const set = (next: PlanNode) => dispatch({ type: "update", f: (p) => updateNode(p, node.id, () => next) });
  const op = (r: { ok: true; plan: Plan } | { ok: false; message: string }, select?: string) =>
    r.ok
      ? dispatch({ type: "setPlan", plan: r.plan, select })
      : dispatch({ type: "toast", text: r.message, kind: "error" });
  const meta = NODE_META[node.type];
  const own = issues.filter((i) => i.nodeId === node.id);

  return (
    <div className={`insp insp--${node.type}`}>
      <div className="insp-head">
        <span className={`sym sym--${node.type}`} /> {meta.label}
        <button className="x x--big" title="Close" onClick={() => dispatch({ type: "select", id: null })}>
          ×
        </button>
      </div>
      <p className="insp-blurb">{meta.blurb}</p>
      <IoLine plan={plan} node={node} side="in" />
      {own.length > 0 && (
        <ul className="insp-issues">
          {own.map((i, k) => (
            <li key={k} className={i.severity}>
              {i.message}
            </li>
          ))}
        </ul>
      )}
      <Field label="Title">
        <input
          value={node.title ?? ""}
          placeholder={meta.label}
          onChange={(e) => set({ ...node, title: e.target.value || undefined })}
        />
      </Field>
      <InputsEditor plan={plan} node={node} op={op} />

      {node.type === "input_logs" && <InputLogsForm node={node} set={set} />}
      {node.type === "classifier" && (
        <ClassifierForm
          node={node}
          set={set}
          limits={plan.limits}
          setLimits={(limits) => dispatch({ type: "update", f: (p) => ({ ...p, limits }) })}
        />
      )}
      {node.type === "run_python" && <RunPythonForm plan={plan} node={node} set={set} op={op} />}
      {node.type === "run_llm" && <RunLlmForm plan={plan} node={node} set={set} op={op} />}
      {node.type === "final_report" && <ReportForm plan={plan} node={node} set={set} />}

      <IoLine plan={plan} node={node} side="out" />
      <details className="f">
        <summary className="f-label">Python</summary>
        <span className="f-hint">
          This block as the Python file the box runs. Read only for now: change the settings above and it follows.
        </span>
        <pre className="insp-py">{blockPython(plan, node)}</pre>
      </details>
      <div className="insp-foot">
        <button
          className="linklike danger"
          onClick={() => dispatch({ type: "update", f: (p) => deleteNodes(p, [node.id]) })}
        >
          Delete node
        </button>
      </div>
    </div>
  );
}

type Op = (r: { ok: true; plan: Plan } | { ok: false; message: string }, select?: string) => void;

/** What comes into the block (top of the pane) or what goes out (bottom), as a short list. */
function IoLine({ plan, node, side }: { plan: Plan; node: PlanNode; side: "in" | "out" }) {
  const { inputs, output } = describeNode(plan, node);
  if (side === "in") {
    const bound = inputs.filter((i) => i.ref);
    return (
      <div className="insp-io">
        <span className="insp-io-k">Input</span>
        <ul>
          {node.type === "input_logs" ? (
            <li>{sourceLabel(node.config.source)}</li>
          ) : bound.length === 0 ? (
            <li>N/A · not connected</li>
          ) : (
            bound.map((i) => (
              <li key={i.name}>
                <span className="mono">{i.type ?? "?"}</span> from <span className="mono">{i.ref}</span>
                {i.shape && <span className="mono insp-io-shape">{i.shape}</span>}
              </li>
            ))
          )}
        </ul>
      </div>
    );
  }
  return (
    <div className="insp-io">
      <span className="insp-io-k">Output</span>
      <ul>
        {output ? (
          <>
            <li>
              <span className="mono">{output.type === "Report" ? "the report" : output.type}</span>
              <span className="mono insp-io-shape">{output.shape}</span>
            </li>
            {node.type === "classifier" && <li>a report can read count, any and proportion per label</li>}
          </>
        ) : (
          <li>nothing</li>
        )}
      </ul>
    </div>
  );
}

function PlanPanel({
  plan,
  issues,
  dispatch,
  onClose,
}: {
  plan: Plan;
  issues: Issue[];
  dispatch: Dispatch<Action>;
  onClose?: () => void;
}) {
  const setMeta = (patch: Partial<Plan>) => dispatch({ type: "update", f: (p) => ({ ...p, ...patch }) });
  return (
    <div className="insp">
      <div className="insp-head">
        Plan
        <span className="bar-spacer" />
        {onClose && (
          <button className="x" title="Close" onClick={onClose}>
            ×
          </button>
        )}
      </div>
      <p className="insp-blurb">Select a block to edit it.</p>
      <Field label="Plan id">
        <CommitInput
          className="mono"
          value={plan.plan_id}
          onCommit={(v) =>
            /^[a-z][a-z0-9_]*$/.test(v)
              ? setMeta({ plan_id: v })
              : dispatch({ type: "toast", text: "plan_id: use lower_snake_case", kind: "error" })
          }
        />
      </Field>
      <Field label="Title">
        <input value={plan.title} onChange={(e) => setMeta({ title: e.target.value })} />
      </Field>
      <Field label="Description">
        <textarea
          rows={3}
          value={plan.description ?? ""}
          onChange={(e) => setMeta({ description: e.target.value || undefined })}
        />
      </Field>
      <details className="f">
        <summary className="f-label">Python</summary>
        <span className="f-hint">The wrapper: which block feeds which, in the order the box runs them. Read only for now.</span>
        <pre className="insp-py">{planPython(plan)}</pre>
      </details>
      <div className="f-label">Issues</div>
      {issues.length === 0 ? (
        <p className="ok-line">No issues.</p>
      ) : (
        <ul className="insp-issues">
          {issues.map((i, k) => (
            <li key={k} className={i.severity}>
              {i.nodeId ? (
                <button className="linklike" onClick={() => dispatch({ type: "select", id: i.nodeId! })}>
                  {i.message}
                </button>
              ) : (
                i.message
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Run Python and Run LLM name their inputs (the names are the arguments); other blocks are wired on the diagram. */
function InputsEditor({ plan, node, op }: { plan: Plan; node: PlanNode; op: Op }) {
  const rule = INPUT_RULES[node.type];
  if (rule.kind !== "dynamic") return null;
  // Outputs this node could read without a cycle or a type mismatch.
  const candidates = plan.nodes
    .map((s) => ({ s, out: outputPort(s) }))
    .filter(
      ({ s, out }) => out && s.id !== node.id && accepts(node.type, out.type) && !wouldCycle(plan, s.id, node.id),
    );
  const names = Object.keys(node.inputs);
  return (
    <div className="f">
      <span className="f-label">Reads from</span>
      <table className="io">
        <tbody>
          {names.map((name) => (
            <tr key={name}>
              <td>
                <CommitInput
                  className="mono"
                  value={name}
                  onCommit={(v) => op(renameInput(plan, node.id, name, v))}
                />
              </td>
              <td className="io-arrow">←</td>
              <td>
                <select
                  value={node.inputs[name] ?? ""}
                  onChange={(e) => {
                    const v = e.target.value;
                    if (!v) return op({ ok: true, plan: disconnect(plan, node.id, name) });
                    op(connect(plan, { source: v.split(".")[0], target: node.id, targetHandle: name }));
                  }}
                >
                  <option value="">— not connected —</option>
                  {node.inputs[name] &&
                    !candidates.some(({ s, out }) => `${s.id}.${out!.name}` === node.inputs[name]) && (
                      <option value={node.inputs[name]}>{node.inputs[name]}</option>
                    )}
                  {candidates.map(({ s, out }) => (
                    <option key={s.id} value={`${s.id}.${out!.name}`}>
                      {s.id}.{out!.name} ({out!.type})
                    </option>
                  ))}
                </select>
                {node.inputs[name] && (
                  <span className="io-type">
                    <TypeBadge type={inputType(plan, node, name)} />
                  </span>
                )}
              </td>
              <td>
                <button
                  className="x"
                  title="Remove input"
                  onClick={() => op({ ok: true, plan: disconnect(plan, node.id, name) })}
                >
                  ×
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {candidates.length > 0 && (
        <select
          className="add-input"
          value=""
          onChange={(e) =>
            e.target.value && op(connect(plan, { source: e.target.value, target: node.id, targetHandle: "__new__" }))
          }
        >
          <option value="">+ read from another block…</option>
          {candidates.map(({ s, out }) => (
            <option key={s.id} value={s.id}>
              {s.id}.{out!.name} ({out!.type})
            </option>
          ))}
        </select>
      )}
    </div>
  );
}

function InputLogsForm({ node, set }: { node: InputLogs; set: (n: PlanNode) => void }) {
  const c = node.config;
  const cfg = (patch: Partial<InputLogs["config"]>) => set({ ...node, config: { ...c, ...patch } });
  return (
    <>
      <Field label="Log source" hint="The logs this agreement may read. A run takes its records from here.">
        <select value={c.source} onChange={(e) => cfg({ source: e.target.value })}>
          {!SOURCE_OPTIONS.some((o) => o.id === c.source) && <option value={c.source}>{c.source} (not a log source)</option>}
          {SOURCE_OPTIONS.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </select>
      </Field>
      <details className="f">
        <summary className="f-label">Narrow it down</summary>
        <div className="insp-more">
      <Field label="Agents (one per line, empty = all)" hint="The agent ids whose logs this plan may read.">
        <LinesInput
          value={c.input_scope.agents}
          onChange={(agents) => cfg({ input_scope: { ...c.input_scope, agents } })}
        />
      </Field>
      <div className="f">
        <span className="f-label">Record kinds</span>
        <div className="checks">
          {RECORD_KINDS.map((k) => (
            <label key={k}>
              <input
                type="checkbox"
                checked={c.filter.kinds.includes(k)}
                onChange={(e) =>
                  cfg({
                    filter: {
                      ...c.filter,
                      kinds: RECORD_KINDS.filter((x) => (x === k ? e.target.checked : c.filter.kinds.includes(x))),
                    },
                  })
                }
              />
              {k}
            </label>
          ))}
        </div>
      </div>
      <Field label="Keywords (one per line, empty = all)">
        <LinesInput value={c.filter.keywords} onChange={(keywords) => cfg({ filter: { ...c.filter, keywords } })} />
      </Field>
      <div className="row2">
        <Field label="Window start">
          <input
            className="mono"
            placeholder="2026-10-01T00:00:00Z"
            value={c.input_scope.window_start ?? ""}
            onChange={(e) => cfg({ input_scope: { ...c.input_scope, window_start: e.target.value || undefined } })}
          />
        </Field>
        <Field label="Window end">
          <input
            className="mono"
            placeholder="(open)"
            value={c.input_scope.window_end ?? ""}
            onChange={(e) => cfg({ input_scope: { ...c.input_scope, window_end: e.target.value || undefined } })}
          />
        </Field>
      </div>
        </div>
      </details>
    </>
  );
}

function ClassifierForm({
  node,
  set,
  limits,
  setLimits,
}: {
  node: Classifier;
  set: (n: PlanNode) => void;
  limits: Plan["limits"];
  setLimits: (l: Plan["limits"]) => void;
}) {
  const c = node.config;
  const cfg = (patch: Partial<Classifier["config"]>) => set({ ...node, config: { ...c, ...patch } });
  return (
    <>
      <div className="f">
        <span className="f-label">Query format</span>
        <div className="row2">
          <label className="f-hint">
            one question per
            <select value={c.group_by} onChange={(e) => cfg({ group_by: e.target.value as Classifier["config"]["group_by"] })}>
              <option value="session">session</option>
              <option value="record">record</option>
            </select>
          </label>
          <label className="f-hint">
            records per question (1 to 32)
            <NumberInput value={c.window} min={1} max={32} onChange={(window) => cfg({ window })} />
          </label>
        </div>
        <div className="row3">
          <label className="f-hint">
            model calls per run (up to 256)
            <NumberInput
              value={limits.max_model_calls_per_run}
              min={1}
              max={256}
              onChange={(v) => setLimits({ ...limits, max_model_calls_per_run: v })}
            />
          </label>
          <label className="f-hint">
            input bytes
            <NumberInput
              value={limits.max_input_utf8_bytes}
              min={1}
              max={1000000000000}
              onChange={(v) => setLimits({ ...limits, max_input_utf8_bytes: v })}
            />
          </label>
          <label className="f-hint">
            seconds (up to 300)
            <NumberInput
              value={limits.max_runtime_seconds_per_run}
              min={1}
              max={300}
              onChange={(v) => setLimits({ ...limits, max_runtime_seconds_per_run: v })}
            />
          </label>
        </div>
        <span className="f-hint">
          Each question is one model call. The box takes at most 2,000 records per run, and refuses a run with more
          questions than the call limit or more input than the byte limit.
        </span>
      </div>
      <Field label="Instructions" hint="The question the model answers about each item. Examples go here too.">
        <textarea rows={5} value={c.instructions} onChange={(e) => cfg({ instructions: e.target.value })} />
      </Field>
      <div className="f">
        <span className="f-label">Labels</span>
        <table className="io labels">
          <thead>
            <tr>
              <th>name</th>
              <th>description</th>
              <th title="Counts as a finding">flag</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {c.labels.map((l, k) => {
              const put = (patch: Partial<typeof l>) =>
                cfg({ labels: c.labels.map((x, j) => (j === k ? { ...x, ...patch } : x)) });
              return (
                <tr key={k}>
                  <td>
                    <CommitInput
                      className="mono"
                      value={l.name}
                      onCommit={(name) => /^[a-z][a-z0-9_]*$/.test(name) && put({ name })}
                    />
                  </td>
                  <td>
                    <input value={l.description} onChange={(e) => put({ description: e.target.value })} />
                  </td>
                  <td>
                    <input type="checkbox" checked={l.flag} onChange={(e) => put({ flag: e.target.checked })} />
                  </td>
                  <td>
                    <button className="x" onClick={() => cfg({ labels: c.labels.filter((_, j) => j !== k) })}>
                      ×
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <button
          className="btn btn--ghost btn--sm"
          onClick={() => {
            const names = new Set(c.labels.map((l) => l.name));
            let k = c.labels.length + 1;
            while (names.has(`label_${k}`)) k++;
            cfg({ labels: [...c.labels, { name: `label_${k}`, description: "", flag: false }] });
          }}
        >
          + label
        </button>
      </div>
      <Field label="Confidence threshold" hint="Below it the item is labelled “abstain”.">
        <div className="inline-check">
          <input
            type="checkbox"
            checked={c.threshold !== undefined}
            onChange={(e) => cfg({ threshold: e.target.checked ? 0.7 : undefined })}
          />
          {c.threshold !== undefined && (
            <NumberInput value={c.threshold} min={0} max={1} step={0.05} onChange={(threshold) => cfg({ threshold })} />
          )}
        </div>
      </Field>
    </>
  );
}

function OutputEditor({
  plan,
  node,
  op,
  set,
}: {
  plan: Plan;
  node: RunPython | RunLlm;
  op: Op;
  set: (n: PlanNode) => void;
}) {
  const out = node.config.output;
  const put = (o: Output) => set({ ...node, config: { ...node.config, output: o } } as PlanNode);
  const fields = Object.entries(out.fields);
  return (
    <div className="f">
      <span className="f-label">Output</span>
      <div className="row2">
        <CommitInput className="mono" value={out.name} onCommit={(v) => op(renameOutput(plan, node.id, v))} />
        <select value={out.type} onChange={(e) => put({ ...out, type: e.target.value as Output["type"] })}>
          {PORT_TYPES.map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
      </div>
      {out.type === "Value" && (
        <>
          <table className="io">
            <tbody>
              {fields.map(([name, t]) => (
                <tr key={name}>
                  <td>
                    <CommitInput
                      className="mono"
                      value={name}
                      onCommit={(v) =>
                        /^[a-z][a-z0-9_]*$/.test(v) &&
                        !(v in out.fields) &&
                        put({ ...out, fields: Object.fromEntries(fields.map(([k, x]) => [k === name ? v : k, x])) })
                      }
                    />
                  </td>
                  <td>
                    <select
                      value={t}
                      onChange={(e) => put({ ...out, fields: { ...out.fields, [name]: e.target.value as FieldType } })}
                    >
                      {FIELD_TYPES.map((f) => (
                        <option key={f}>{f}</option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <button
                      className="x"
                      onClick={() => put({ ...out, fields: Object.fromEntries(fields.filter(([k]) => k !== name)) })}
                    >
                      ×
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button
            className="btn btn--ghost btn--sm"
            onClick={() => {
              let k = fields.length + 1;
              while (`field_${k}` in out.fields) k++;
              put({ ...out, fields: { ...out.fields, [`field_${k}`]: "number" } });
            }}
          >
            + field
          </button>
        </>
      )}
    </div>
  );
}

function RunPythonForm({ plan, node, set, op }: { plan: Plan; node: RunPython; set: (n: PlanNode) => void; op: Op }) {
  const args = describeNode(plan, node).inputs;
  return (
    <>
      <Field
        label="Python"
        hint={
          <>
            Called as <code>run({args.map((a) => a.name).join(", ") || "…"})</code>
            {args.length ? ", where each argument is:" : "."}
            {args.length > 0 && (
              <ul className="arglist">
                {args.map((a) => (
                  <li key={a.name}>
                    <span className="mono">{a.name}</span> <TypeBadge type={a.type} />{" "}
                    {a.shape ?? (a.ref ? "" : "not connected")}
                  </li>
                ))}
              </ul>
            )}
          </>
        }
      >
        <PythonEditor
          value={node.config.code}
          onChange={(code) => set({ ...node, config: { ...node.config, code } })}
        />
      </Field>
      <OutputEditor plan={plan} node={node} op={op} set={set} />
    </>
  );
}

function RunLlmForm({ plan, node, set, op }: { plan: Plan; node: RunLlm; set: (n: PlanNode) => void; op: Op }) {
  const c = node.config;
  const cfg = (patch: Partial<RunLlm["config"]>) => set({ ...node, config: { ...c, ...patch } });
  return (
    <>
      <Field label="System prompt">
        <textarea rows={3} value={c.system_prompt} onChange={(e) => cfg({ system_prompt: e.target.value })} />
      </Field>
      <Field
        label="User prompt"
        hint={
          <>
            Insert an input:{" "}
            {Object.keys(node.inputs).map((k) => (
              <button
                key={k}
                className="linklike mono"
                onClick={() => cfg({ user_prompt: `${c.user_prompt}{{${k}}}` })}
              >
                {`{{${k}}}`}
              </button>
            ))}
            {!Object.keys(node.inputs).length && "connect one first"}
          </>
        }
      >
        <textarea
          rows={7}
          className="mono-area"
          value={c.user_prompt}
          onChange={(e) => cfg({ user_prompt: e.target.value })}
        />
      </Field>
      <OutputEditor plan={plan} node={node} op={op} set={set} />
    </>
  );
}

/** What the report's single input exposes, as paths a field can read. */
function reportPaths(plan: Plan, node: FinalReport): { path: string; type: FieldType }[] {
  const src = reportSource(plan, node);
  const out = src && outputPort(src);
  if (!src || !out) return [];
  if ((src.type === "run_python" || src.type === "run_llm") && out.type === "Value")
    return Object.entries(src.config.output.fields).map(([path, type]) => ({ path, type }));
  if (out.type !== "Classifications") return [];
  const labels = classifierLabels(src) ?? [];
  return CLASSIFICATION_PATHS.flatMap((p) =>
    p.path.includes("<label>")
      ? labels.map((l) => ({ path: p.path.replace("<label>", l), type: p.type as FieldType }))
      : [{ path: p.path, type: p.type as FieldType }],
  );
}

function ReportForm({ plan, node, set }: { plan: Plan; node: FinalReport; set: (n: PlanNode) => void }) {
  const c = node.config;
  const cfg = (patch: Partial<FinalReport["config"]>) => set({ ...node, config: { ...c, ...patch } });
  const paths = reportPaths(plan, node);
  const typeOf = (p: string) => paths.find((x) => x.path === p)?.type;
  return (
    <>
      <div className="f">
        <span className="f-label">Released fields — the shape of the result</span>
        <table className="io">
          <thead>
            <tr>
              <th>name</th>
              <th>from</th>
              <th>type</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {c.fields.map((f, k) => {
              const put = (patch: Partial<typeof f>) =>
                cfg({ fields: c.fields.map((x, j) => (j === k ? { ...x, ...patch } : x)) });
              return (
                <tr key={k}>
                  <td>
                    <CommitInput
                      className="mono"
                      value={f.name}
                      onCommit={(name) => /^[a-z][a-z0-9_]*$/.test(name) && put({ name })}
                    />
                  </td>
                  <td>
                    <select
                      value={fieldPath(f)}
                      onChange={(e) => {
                        const path = e.target.value;
                        put({ from: path === f.name ? undefined : path, type: typeOf(path) ?? f.type });
                      }}
                    >
                      {!paths.some((p) => p.path === fieldPath(f)) && (
                        <option value={fieldPath(f)}>{fieldPath(f)} (unresolved)</option>
                      )}
                      {paths.map((p) => (
                        <option key={p.path} value={p.path}>
                          {p.path}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <select value={f.type} onChange={(e) => put({ type: e.target.value as FieldType })}>
                      {FIELD_TYPES.map((t) => (
                        <option key={t}>{t}</option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <button className="x" onClick={() => cfg({ fields: c.fields.filter((_, j) => j !== k) })}>
                      ×
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <button
          className="btn btn--ghost btn--sm"
          disabled={!paths.length}
          onClick={() => {
            const used = new Set(c.fields.map(fieldPath));
            const p = paths.find((x) => !used.has(x.path)) ?? paths[0];
            const base = p.path.replace(/\./g, "_");
            const names = new Set(c.fields.map((f) => f.name));
            let name = base;
            for (let k = 2; names.has(name); k++) name = `${base}_${k}`;
            cfg({ fields: [...c.fields, { name, type: p.type, ...(name === p.path ? {} : { from: p.path }) }] });
          }}
        >
          + field
        </button>
      </div>
      <Field label="Min count" hint="A count below this number is reported as 0. With 5, a label seen in 3 sessions shows 0, so no single session can be picked out.">
        <NumberInput
          value={c.privacy.min_count}
          min={1}
          onChange={(min_count) => cfg({ privacy: { ...c.privacy, min_count } })}
        />
      </Field>
      <Field label="Max result bytes" hint="A report larger than this is not released. Up to 4096.">
        <NumberInput
          value={c.privacy.max_result_bytes}
          min={1}
          max={4096}
          onChange={(max_result_bytes) => cfg({ privacy: { ...c.privacy, max_result_bytes } })}
        />
      </Field>
      <label className="inline-check">
        <input
          type="checkbox"
          checked={c.privacy.allow_free_text}
          onChange={(e) => cfg({ privacy: { ...c.privacy, allow_free_text: e.target.checked } })}
        />
        Allow text fields written by the model
      </label>
    </>
  );
}
