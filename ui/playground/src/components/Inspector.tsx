import { type Dispatch, useEffect, useState } from "react";
import { AGREEMENT } from "../agreement";
import { connect, deleteNodes, disconnect, renameInput, renameOutput, updateNode } from "../plan/ops";
import { accepts, INPUT_RULES, inputType, outputPort } from "../plan/ports";
import { CLASSIFICATION_PATHS, classifierLabels, fieldPath, reportSource } from "../plan/report";
import { blockPython, narrowing, planPython } from "../plan/python";
import { isQuery, parseQuery, type Query, QUERY_WINDOW_MAX, queryCode } from "../plan/query";
import { describeNode } from "../plan/shape";
import { outputSpec } from "../plan/spec";
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
import { CommitInput, Field, LinesInput, NumberInput, PythonEditor, Select } from "./fields";
import { BLOCK_META, kindOf, SOURCE_OPTIONS, sourceLabel } from "./nodeMeta";
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
  const setLimits = (limits: Plan["limits"]) => dispatch({ type: "update", f: (p) => ({ ...p, limits }) });
  const query = parseQuery(node);
  const kind = kindOf(node);
  const meta = BLOCK_META[kind];
  const own = issues.filter((i) => i.nodeId === node.id);

  return (
    <div className={`insp insp--${node.type}`}>
      <div className="insp-head">
        <span className={`sym sym--${kind}`} /> {meta.label}
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
          setLimits={setLimits}
          fromItems={inputType(plan, node, "items") === "Items"}
          hasQuery={plan.nodes.some(isQuery)}
        />
      )}
      {node.type === "run_python" && query && (
        <QueryForm node={node} query={query} set={set} limits={plan.limits} setLimits={setLimits} />
      )}
      {node.type === "run_python" && !query && <RunPythonForm plan={plan} node={node} set={set} op={op} />}
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
  const spec = outputSpec(node);
  return (
    <div className="insp-io">
      <span className="insp-io-k">Output specification</span>
      <span className="insp-io-kind">
        {output ? <span className="mono">{output.type === "Report" ? "Report" : output.type}</span> : null} · {spec.kind}
      </span>
      {spec.fields.length ? (
        <table className="insp-spec">
          <tbody>
            {spec.fields.map((f) => (
              <tr key={f.name}>
                <td className="mono">{f.name}</td>
                <td className="mono">{f.type}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <span className="is-unbound">no fields declared yet</span>
      )}
      {node.type === "classifier" && <span>A report can read count, any and proportion per label.</span>}
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
  // Blocks this one could read without a cycle or a type mismatch, named by their titles.
  const candidates = plan.nodes
    .map((s) => ({ s, out: outputPort(s) }))
    .filter(
      ({ s, out }) => out && s.id !== node.id && accepts(node.type, out.type) && !wouldCycle(plan, s.id, node.id),
    );
  const label = (id: string) => plan.nodes.find((n) => n.id === id)?.title || id;
  const names = Object.keys(node.inputs);
  // A Query format block reads one block; Code and LLM blocks may read several, each
  // under the name their code or prompt uses.
  const single = isQuery(node);
  const named = !single && names.length > 0;
  return (
    <div className="f">
      <span className="f-label">Reads from</span>
      {names.map((name) => (
        <div key={name} className="reads">
          <Select
            value={node.inputs[name] ?? ""}
            onChange={(e) => {
              const v = e.target.value;
              if (!v) return op({ ok: true, plan: disconnect(plan, node.id, name) });
              op(connect(plan, { source: v.split(".")[0], target: node.id, targetHandle: name }));
            }}
          >
            <option value="">nothing</option>
            {node.inputs[name] &&
              !candidates.some(({ s, out }) => `${s.id}.${out!.name}` === node.inputs[name]) && (
                <option value={node.inputs[name]}>{label(node.inputs[name].split(".")[0])}</option>
              )}
            {candidates.map(({ s, out }) => (
              <option key={s.id} value={`${s.id}.${out!.name}`}>
                {label(s.id)}
              </option>
            ))}
          </Select>
          {named && (
            <span className="f-hint">
              as{" "}
              <CommitInput className="mono reads-name" value={name} onCommit={(v) => op(renameInput(plan, node.id, name, v))} />
            </span>
          )}
        </div>
      ))}
      {candidates.length > 0 && (!single || names.length === 0) && (
        <Select
          value=""
          onChange={(e) =>
            e.target.value && op(connect(plan, { source: e.target.value, target: node.id, targetHandle: "__new__" }))
          }
        >
          <option value="">{names.length ? "add another block" : "nothing"}</option>
          {candidates.map(({ s }) => (
            <option key={s.id} value={s.id}>
              {label(s.id)}
            </option>
          ))}
        </Select>
      )}
    </div>
  );
}

function InputLogsForm({ node, set }: { node: InputLogs; set: (n: PlanNode) => void }) {
  const c = node.config;
  const cfg = (patch: Partial<InputLogs["config"]>) => set({ ...node, config: { ...c, ...patch } });
  const narrowed = narrowing(node);
  return (
    <>
      <Field label="Log source" hint="The logs this agreement may read. A run takes its records from here.">
        <Select value={c.source} onChange={(e) => cfg({ source: e.target.value })}>
          {!SOURCE_OPTIONS.some((o) => o.id === c.source) && <option value={c.source}>{c.source} (not a log source)</option>}
          {SOURCE_OPTIONS.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </Select>
      </Field>
      <SessionPicker
        source={c.source}
        value={c.filter.sessions ?? []}
        onChange={(sessions) => {
          const { sessions: _drop, ...rest } = c.filter;
          cfg({ filter: sessions.length ? { ...rest, sessions } : rest });
        }}
      />
      {narrowed.length > 0 && (
        <span className="f-hint">
          An earlier version of this agreement also narrows by {narrowed.join(", ")}.{" "}
          <button
            className="linklike"
            onClick={() =>
              cfg({
                input_scope: { agents: [] },
                filter: { kinds: [...RECORD_KINDS], keywords: [], ...(c.filter.sessions ? { sessions: c.filter.sessions } : {}) },
              })
            }
          >
            Remove that
          </button>
        </span>
      )}
    </>
  );
}

function LimitsFields({ limits, setLimits }: { limits: Plan["limits"]; setLimits: (l: Plan["limits"]) => void }) {
  return (
    <>
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
    </>
  );
}

/** A Query format block: its two settings rewrite the block's code; the limits are the plan's. */
function QueryForm({
  node,
  query,
  set,
  limits,
  setLimits,
}: {
  node: RunPython;
  query: Query;
  set: (n: PlanNode) => void;
  limits: Plan["limits"];
  setLimits: (l: Plan["limits"]) => void;
}) {
  const put = (patch: Partial<Query>) =>
    set({ ...node, config: { ...node.config, code: queryCode({ ...query, ...patch }) } });
  return (
    <div className="f">
      <div className="row2">
        <label className="f-hint">
          one question per
          <Select value={query.per} onChange={(e) => put({ per: e.target.value as Query["per"] })}>
            <option value="session">session</option>
            <option value="record">record</option>
          </Select>
        </label>
        <label className="f-hint">
          records per question (1 to 32)
          <NumberInput value={query.window} min={1} max={QUERY_WINDOW_MAX} onChange={(window) => put({ window })} />
        </label>
      </div>
      <LimitsFields limits={limits} setLimits={setLimits} />
    </div>
  );
}

interface SessionRow {
  id: string;
  title: string;
  who: string;
  started: string;
  records: number;
}

/**
 * Which sessions of the log source the agreement reads: none ticked means all of them.
 * The list comes from the agreement UI by metadata only; without a server (the standalone
 * playground) the ids are typed one per line.
 */
function SessionPicker({
  source,
  value,
  onChange,
}: {
  source: string;
  value: string[];
  onChange: (sessions: string[]) => void;
}) {
  const [rows, setRows] = useState<SessionRow[] | null>(null);
  const [q, setQ] = useState("");
  useEffect(() => {
    let live = true;
    setRows(null);
    if (!AGREEMENT) return;
    fetch(`/logs/sessions.json?logs=${encodeURIComponent(source)}`, { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => live && setRows(body && body.source === source ? (body.sessions as SessionRow[]) : []))
      .catch(() => live && setRows([]));
    return () => {
      live = false;
    };
  }, [source]);
  const chosen = new Set(value);
  const known = new Set((rows ?? []).map((r) => r.id));
  const gone = value.filter((id) => rows !== null && !known.has(id));
  const needle = q.trim().toLowerCase();
  const shown = (rows ?? []).filter((r) => !needle || `${r.title} ${r.who} ${r.id}`.toLowerCase().includes(needle));
  const toggle = (id: string) => onChange(chosen.has(id) ? value.filter((x) => x !== id) : [...value, id]);
  return (
    <div className="f">
      <span className="f-label">Sessions</span>
      <span className="f-hint">
        {value.length === 0
          ? "Every session of the log source. Tick sessions to read only those."
          : `${value.length} session${value.length === 1 ? "" : "s"} chosen. Untick all to read every session.`}
      </span>
      {!AGREEMENT || rows === null ? (
        !AGREEMENT ? (
          <LinesInput value={value} onChange={onChange} />
        ) : (
          <span className="f-hint">Loading the sessions…</span>
        )
      ) : (
        <>
          {rows.length > 8 && <input value={q} placeholder="Search sessions" onChange={(e) => setQ(e.target.value)} />}
          <div className="sess-pick">
            {shown.map((r) => (
              <label key={r.id}>
                <input type="checkbox" checked={chosen.has(r.id)} onChange={() => toggle(r.id)} />
                <span>
                  {r.title}
                  <span className="muted"> · {r.records} records</span>
                </span>
              </label>
            ))}
            {gone.map((id) => (
              <label key={id}>
                <input type="checkbox" checked onChange={() => toggle(id)} />
                <span className="danger">{id} · not in this log source any more</span>
              </label>
            ))}
            {shown.length === 0 && gone.length === 0 && (
              <span className="f-hint">
                {SOURCE_OPTIONS.some((o) => o.id === source) ? "No session here." : "Pick a log source first."}
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function ClassifierForm({
  node,
  set,
  limits,
  setLimits,
  fromItems,
  hasQuery,
}: {
  node: Classifier;
  set: (n: PlanNode) => void;
  limits: Plan["limits"];
  setLimits: (l: Plan["limits"]) => void;
  /** The input is already Items (a Query format block or another Items output): group_by and window are unused. */
  fromItems: boolean;
  /** The plan has a Query format block, which carries the limits. */
  hasQuery: boolean;
}) {
  const c = node.config;
  const cfg = (patch: Partial<Classifier["config"]>) => set({ ...node, config: { ...c, ...patch } });
  return (
    <>
      {!(fromItems && hasQuery) && (
      <div className="f">
        <span className="f-label">{fromItems ? "Limits" : "Query format"}</span>
        {!fromItems && (
        <div className="row2">
          <label className="f-hint">
            one question per
            <Select value={c.group_by} onChange={(e) => cfg({ group_by: e.target.value as Classifier["config"]["group_by"] })}>
              <option value="session">session</option>
              <option value="record">record</option>
            </Select>
          </label>
          <label className="f-hint">
            records per question (1 to 32)
            <NumberInput value={c.window} min={1} max={32} onChange={(window) => cfg({ window })} />
          </label>
        </div>
        )}
        {!hasQuery && <LimitsFields limits={limits} setLimits={setLimits} />}
      </div>
      )}
      <Field label="Prompt" hint="The question the model answers about each item. Examples go here too.">
        <textarea rows={5} value={c.instructions} onChange={(e) => cfg({ instructions: e.target.value })} />
      </Field>
      <details className="f">
        <summary className="f-label">Answers the model may give</summary>
        <span className="f-hint">
          The model must answer each question with one of these labels, never free text. That is why only counts
          can leave the box. A flagged label counts as a finding in the report.
        </span>
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
      </details>
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
        <Select value={out.type} onChange={(e) => put({ ...out, type: e.target.value as Output["type"] })}>
          {PORT_TYPES.map((t) => (
            <option key={t}>{t}</option>
          ))}
        </Select>
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
                    <Select
                      value={t}
                      onChange={(e) => put({ ...out, fields: { ...out.fields, [name]: e.target.value as FieldType } })}
                    >
                      {FIELD_TYPES.map((f) => (
                        <option key={f}>{f}</option>
                      ))}
                    </Select>
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
                    <Select
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
                    </Select>
                  </td>
                  <td>
                    <Select value={f.type} onChange={(e) => put({ type: e.target.value as FieldType })}>
                      {FIELD_TYPES.map((t) => (
                        <option key={t}>{t}</option>
                      ))}
                    </Select>
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
