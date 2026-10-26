import { type Dispatch, useMemo } from "react";
import { LOG_SOURCES, selectRecords, sourceAgents } from "../plan/mockLogs";
import { connect, deleteNodes, disconnect, renameInput, renameNode, renameOutput, updateNode } from "../plan/ops";
import { accepts, INPUT_RULES, inputType, outputPort } from "../plan/ports";
import { CLASSIFICATION_PATHS, classifierLabels, fieldPath, reportJsonSchema, reportSource } from "../plan/report";
import { simulate } from "../plan/simulate";
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
import { Advanced, CommitInput, Field, LinesInput, NumberInput, PythonEditor } from "./fields";
import { MODEL_OPTIONS, NODE_META } from "./nodeMeta";

interface Props {
  plan: Plan;
  selected: string | null;
  issues: Issue[];
  dispatch: Dispatch<Action>;
}

export default function Inspector({ plan, selected, issues, dispatch }: Props) {
  const node = plan.nodes.find((n) => n.id === selected);
  if (!node) return <PlanPanel plan={plan} issues={issues} dispatch={dispatch} />;

  const set = (next: PlanNode) => dispatch({ type: "update", f: (p) => updateNode(p, node.id, () => next) });
  const op = (r: { ok: true; plan: Plan } | { ok: false; message: string }, select?: string) =>
    r.ok
      ? dispatch({ type: "setPlan", plan: r.plan, select })
      : dispatch({ type: "toast", text: r.message, kind: "error" });
  const meta = NODE_META[node.type];
  const own = issues.filter((i) => i.nodeId === node.id);

  return (
    <div className={`insp insp--${node.type}`}>
      <div className="insp-head" title={meta.blurb}>
        <span className="pn-glyph">{meta.glyph}</span> {meta.label}
        <CommitInput className="insp-id mono" value={node.id} onCommit={(v) => op(renameNode(plan, node.id, v), v)} />
        <button className="x" title="Close" onClick={() => dispatch({ type: "select", id: null })}>
          ×
        </button>
      </div>
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
      {node.type === "classifier" && <ClassifierForm node={node} set={set} />}
      {node.type === "run_python" && <RunPythonForm plan={plan} node={node} set={set} op={op} />}
      {node.type === "run_llm" && <RunLlmForm plan={plan} node={node} set={set} op={op} />}
      {node.type === "final_report" && <ReportForm plan={plan} node={node} set={set} />}

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

function PlanPanel({ plan, issues, dispatch }: { plan: Plan; issues: Issue[]; dispatch: Dispatch<Action> }) {
  const setMeta = (patch: Partial<Plan>) => dispatch({ type: "update", f: (p) => ({ ...p, ...patch }) });
  return (
    <div className="insp">
      <div className="insp-head">Plan</div>
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

function InputsEditor({ plan, node, op }: { plan: Plan; node: PlanNode; op: Op }) {
  const rule = INPUT_RULES[node.type];
  if (rule.kind === "none") return null;
  // Outputs this node could read without a cycle or a type mismatch.
  const candidates = plan.nodes
    .map((s) => ({ s, out: outputPort(s) }))
    .filter(
      ({ s, out }) => out && s.id !== node.id && accepts(node.type, out.type) && !wouldCycle(plan, s.id, node.id),
    );
  const names = rule.kind === "fixed" ? rule.ports : Object.keys(node.inputs);
  return (
    <div className="f">
      <span className="f-label">Inputs</span>
      <table className="io">
        <tbody>
          {names.map((name) => (
            <tr key={name}>
              <td>
                {rule.kind === "dynamic" ? (
                  <CommitInput
                    className="mono"
                    value={name}
                    onCommit={(v) => op(renameInput(plan, node.id, name, v))}
                  />
                ) : (
                  <span className="mono">{name}</span>
                )}
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
                {node.inputs[name] && <span className="io-type">{inputType(plan, node, name)}</span>}
              </td>
              {rule.kind === "dynamic" && (
                <td>
                  <button
                    className="x"
                    title="Remove input"
                    onClick={() => op({ ok: true, plan: disconnect(plan, node.id, name) })}
                  >
                    ×
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {rule.kind === "dynamic" && candidates.length > 0 && (
        <select
          className="add-input"
          value=""
          onChange={(e) =>
            e.target.value && op(connect(plan, { source: e.target.value, target: node.id, targetHandle: "__new__" }))
          }
        >
          <option value="">+ add input from…</option>
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

function ModelField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <Field label="Model" hint="Mock list; the box pins one model per plan.">
      <input list="models" className="mono" value={value} onChange={(e) => onChange(e.target.value)} />
      <datalist id="models">
        {MODEL_OPTIONS.map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>
    </Field>
  );
}

function InputLogsForm({ node, set }: { node: InputLogs; set: (n: PlanNode) => void }) {
  const c = node.config;
  const cfg = (patch: Partial<InputLogs["config"]>) => set({ ...node, config: { ...c, ...patch } });
  const agents = sourceAgents(c.source);
  const records = useMemo(() => selectRecords(node), [node]);
  return (
    <>
      <Field label="Log source">
        <select
          value={c.source}
          onChange={(e) => cfg({ source: e.target.value, input_scope: { ...c.input_scope, agents: [] } })}
        >
          {!LOG_SOURCES[c.source] && <option value={c.source}>{c.source} (unknown)</option>}
          {Object.entries(LOG_SOURCES).map(([k, s]) => (
            <option key={k} value={k}>
              {s.label}
            </option>
          ))}
        </select>
      </Field>
      <div className="f">
        <span className="f-label">Agents (none ticked = all)</span>
        <div className="checks">
          {agents.map((a) => (
            <label key={a}>
              <input
                type="checkbox"
                checked={c.input_scope.agents.includes(a)}
                onChange={(e) =>
                  cfg({
                    input_scope: {
                      ...c.input_scope,
                      agents: e.target.checked
                        ? [...c.input_scope.agents, a]
                        : c.input_scope.agents.filter((x) => x !== a),
                    },
                  })
                }
              />
              {a}
            </label>
          ))}
        </div>
      </div>
      <Field label="Keywords (one per line, empty = all)">
        <LinesInput value={c.filter.keywords} onChange={(keywords) => cfg({ filter: { ...c.filter, keywords } })} />
      </Field>
      <Advanced>
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
        <Field label="Max records">
          <NumberInput value={c.max_records} min={1} onChange={(max_records) => cfg({ max_records })} />
        </Field>
      </Advanced>
      <div className="f">
        <span className="f-label">Preview · {records.length} synthetic records</span>
        <div className="rec-preview">
          <table>
            <thead>
              <tr>
                <th>id</th>
                <th>agent / session</th>
                <th>kind</th>
                <th>content</th>
              </tr>
            </thead>
            <tbody>
              {records.slice(0, 40).map((r) => (
                <tr key={r.id}>
                  <td className="mono">{r.id}</td>
                  <td className="mono">
                    {r.agent_id}/{r.session_id}
                  </td>
                  <td>{r.kind}</td>
                  <td>{r.content}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

function ClassifierForm({ node, set }: { node: Classifier; set: (n: PlanNode) => void }) {
  const c = node.config;
  const cfg = (patch: Partial<Classifier["config"]>) => set({ ...node, config: { ...c, ...patch } });
  const contract = {
    item_id: "s01",
    label:
      c.labels
        .map((l) => l.name)
        .concat(c.threshold !== undefined ? ["abstain"] : [])
        .join(" | ") || "…",
    ...(c.output_contract.score ? { score: "0.0 – 1.0" } : {}),
    ...(c.output_contract.evidence_ids ? { evidence_ids: ["r001", "r003"] } : {}),
  };
  return (
    <>
      <Field label="Instructions" hint="The question the classifier answers about each item.">
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
      <Advanced>
        <ModelField value={c.model.id} onChange={(id) => cfg({ model: { ...c.model, id } })} />
        <div className="row2">
          <Field label="Classify each">
            <select value={c.group_by} onChange={(e) => cfg({ group_by: e.target.value as "session" | "record" })}>
              <option value="session">session</option>
              <option value="record">record</option>
            </select>
          </Field>
          <Field label="Window (records/call)">
            <NumberInput value={c.window} min={1} onChange={(window) => cfg({ window })} />
          </Field>
        </div>
        <Field label="Output contract">
          <div className="checks">
            <label>
              <input type="checkbox" checked disabled /> label
            </label>
            <label>
              <input
                type="checkbox"
                checked={c.output_contract.score}
                onChange={(e) => cfg({ output_contract: { ...c.output_contract, score: e.target.checked } })}
              />{" "}
              score
            </label>
            <label>
              <input
                type="checkbox"
                checked={c.output_contract.evidence_ids}
                onChange={(e) => cfg({ output_contract: { ...c.output_contract, evidence_ids: e.target.checked } })}
              />{" "}
              evidence_ids
            </label>
          </div>
        </Field>
        <pre className="contract">{JSON.stringify(contract, null, 2)}</pre>
        <Field label="Positive examples" hint="One per line: should get a flagged label.">
          <LinesInput value={c.positive_examples} onChange={(positive_examples) => cfg({ positive_examples })} />
        </Field>
        <Field label="Negative examples" hint="One per line: should not be flagged.">
          <LinesInput value={c.negative_examples} onChange={(negative_examples) => cfg({ negative_examples })} />
        </Field>
      </Advanced>
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
  const args = Object.keys(node.inputs);
  return (
    <>
      <Field
        label="Python"
        hint={
          <>
            Called as <code>run({args.join(", ") || "…"})</code>.
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
      <Advanced>
        <ModelField value={c.model.id} onChange={(id) => cfg({ model: { ...c.model, id } })} />
        <div className="row2">
          <Field label="Temperature">
            <NumberInput
              value={c.model.temperature}
              min={0}
              max={2}
              step={0.1}
              onChange={(temperature) => cfg({ model: { ...c.model, temperature } })}
            />
          </Field>
          <Field label="Max output tokens">
            <NumberInput
              value={c.model.max_output_tokens}
              min={1}
              onChange={(max_output_tokens) => cfg({ model: { ...c.model, max_output_tokens } })}
            />
          </Field>
        </div>
      </Advanced>
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
  const sim = useMemo(() => simulate(plan), [plan]);
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
      <Advanced>
        <div className="f">
          <span className="f-label">Release policy</span>
          <label className="inline-check">
            <input
              type="checkbox"
              checked={c.privacy.allow_free_text}
              onChange={(e) => cfg({ privacy: { ...c.privacy, allow_free_text: e.target.checked } })}
            />
            Allow free-text (string) fields
          </label>
          <div className="row2">
            <Field label="Min count" hint="Smaller counts are released as 0.">
              <NumberInput
                value={c.privacy.min_count}
                min={1}
                onChange={(min_count) => cfg({ privacy: { ...c.privacy, min_count } })}
              />
            </Field>
            <Field label="Max result bytes">
              <NumberInput
                value={c.privacy.max_result_bytes}
                min={1}
                onChange={(max_result_bytes) => cfg({ privacy: { ...c.privacy, max_result_bytes } })}
              />
            </Field>
          </div>
        </div>
        <details className="f">
          <summary className="f-label">Output schema (JSON Schema)</summary>
          <pre className="contract">{JSON.stringify(reportJsonSchema(node), null, 2)}</pre>
        </details>
      </Advanced>
      <div className="f">
        <span className="f-label">
          Preview <span className="sim-badge">simulated</span>
        </span>
        <pre className="contract">{JSON.stringify(sim.report, null, 2)}</pre>
        <span className={`f-hint${sim.reportBytes > c.privacy.max_result_bytes ? " danger" : ""}`}>
          {sim.reportBytes} of {c.privacy.max_result_bytes} bytes. Mock values from synthetic logs; nothing was
          executed.
        </span>
      </div>
    </>
  );
}
