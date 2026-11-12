/**
 * The settings of the Filter and AND / OR blocks. A Filter is in Rules mode (rows of field,
 * operator and value, all or any of them, with Exclude) or LLM mode (one yes/no question).
 * Rules mode counts what it keeps from the sample logs; real records are filtered in the box.
 */
import { useEffect, useId, useState } from "react";
import { AGREEMENT } from "../agreement";
import {
  FIELD_LABELS,
  newRule,
  OP_LABELS,
  ruleIssue,
  ruleUnfinished,
  sampleCount,
  withOp,
} from "../plan/filter";
import { sourceRecords } from "../plan/mockLogs";
import { connect, DEFAULT_MODEL, disconnect } from "../plan/ops";
import { accepts, outputPort } from "../plan/ports";
import {
  type CombineLogs,
  FIELD_OPS,
  type FilterLlm,
  type FilterLogs,
  type FilterRules,
  type Plan,
  type PlanNode,
  RECORD_KINDS,
  RULE_FIELDS,
  type Rule,
  type RuleField,
} from "../plan/types";
import { wouldCycle } from "../plan/validate";
import { LinesInput, NumberInput, Segmented, Select } from "./fields";
import { MODEL_OPTIONS } from "./nodeMeta";

type Op = (r: { ok: true; plan: Plan } | { ok: false; message: string }, select?: string) => void;
type Set = (n: PlanNode) => void;

/** A session of a log source, by metadata only (the agreement UI's /logs/sessions.json). */
interface SessionRow {
  id: string;
  title: string;
  who: string;
}

const loaded = new Map<string, Promise<SessionRow[]>>();

/** The sessions of a log source: from the agreement UI, or from the sample logs standalone. */
function loadSessions(source: string): Promise<SessionRow[]> {
  if (!AGREEMENT) {
    const seen = new Map<string, SessionRow>();
    for (const r of sourceRecords(source))
      if (!seen.has(r.session_id)) seen.set(r.session_id, { id: r.session_id, title: r.session_id, who: r.agent_id });
    return Promise.resolve([...seen.values()]);
  }
  if (!loaded.has(source))
    loaded.set(
      source,
      fetch(`/logs/sessions.json?logs=${encodeURIComponent(source)}`, { credentials: "same-origin" })
        .then((r) => (r.ok ? r.json() : null))
        .then((body) => (body && body.source === source ? (body.sessions as SessionRow[]) : []))
        .catch(() => []),
    );
  return loaded.get(source)!;
}

function useSessions(source: string | undefined): SessionRow[] | null {
  const [rows, setRows] = useState<SessionRow[] | null>(null);
  useEffect(() => {
    let live = true;
    setRows(null);
    if (source) loadSessions(source).then((r) => live && setRows(r));
    return () => {
      live = false;
    };
  }, [source]);
  return rows;
}

/** The other mode's settings while this page is open, so switching back restores them. */
const stash = new Map<string, Partial<Record<"rules" | "llm", FilterRules | FilterLlm>>>();

const RULES_DEFAULT: FilterRules = { mode: "rules", match: "all", exclude: false, rules: [] };
const LLM_DEFAULT: FilterLlm = {
  mode: "llm",
  llm: {
    model: { ...DEFAULT_MODEL },
    question: "",
    judge: "record",
    yes_examples: [],
    no_examples: [],
    threshold: 0.7,
    unsure: "drop",
  },
};

export function FilterForm({ plan, node, set, op }: { plan: Plan; node: FilterLogs; set: Set; op: Op }) {
  const c = node.config;
  const mode = (m: "rules" | "llm") => {
    if (m === c.mode) return;
    stash.set(node.id, { ...stash.get(node.id), [c.mode]: c });
    const back = stash.get(node.id)?.[m];
    set({ ...node, config: back ?? (m === "rules" ? RULES_DEFAULT : LLM_DEFAULT) });
  };
  return (
    <>
      <PortPicker plan={plan} node={node} port="records" label="Reads from" op={op} />
      <Segmented
        className="seg--mode"
        label="Filter mode"
        value={c.mode}
        options={[
          { value: "rules", label: "Rules" },
          { value: "llm", label: "LLM" },
        ]}
        onChange={mode}
      />
      {c.mode === "rules" ? (
        <RulesForm plan={plan} node={node} config={c} set={set} />
      ) : (
        <LlmForm node={node} config={c} set={set} />
      )}
    </>
  );
}

function RulesForm({ plan, node, config: c, set }: { plan: Plan; node: FilterLogs; config: FilterRules; set: Set }) {
  const put = (patch: Partial<FilterRules>) => set({ ...node, config: { ...c, ...patch } });
  const putRule = (k: number, rule: Rule) => put({ rules: c.rules.map((r, j) => (j === k ? rule : r)) });
  const source = plan.nodes.find((n) => n.type === "input_logs");
  const sessions = useSessions(source?.type === "input_logs" ? source.config.source : undefined);
  const lead = useId();
  return (
    <div className="f rules-f">
      <div className="rules-head">
        <span id={lead}>{c.exclude ? "Drop" : "Keep"} records where</span>
        <Select className="sel--inline sel--float" label="Match" value={c.match} onChange={(e) => put({ match: e.target.value as FilterRules["match"] })}>
          <option value="all">all</option>
          <option value="any">any</option>
        </Select>
        <span>of:</span>
        <label className="inline-check rules-exclude">
          <input type="checkbox" checked={c.exclude} onChange={(e) => put({ exclude: e.target.checked })} />
          Exclude
        </label>
      </div>
      {c.rules.length === 0 ? (
        <div className="rules-empty">No rules yet</div>
      ) : (
        <ol className="rules" aria-labelledby={lead}>
          {c.rules.map((rule, k) => (
            <RuleRow
              key={k}
              n={k + 1}
              rule={rule}
              sessions={sessions}
              onChange={(r) => putRule(k, r)}
              onRemove={() => put({ rules: c.rules.filter((_, j) => j !== k) })}
            />
          ))}
        </ol>
      )}
      <button type="button" className="btn btn--ghost btn--sm" onClick={() => put({ rules: [...c.rules, newRule("content")] })}>
        + rule
      </button>
      <SampleCount plan={plan} node={node} />
    </div>
  );
}

function RuleRow({
  n,
  rule,
  sessions,
  onChange,
  onRemove,
}: {
  n: number;
  rule: Rule;
  sessions: SessionRow[] | null;
  onChange: (r: Rule) => void;
  onRemove: () => void;
}) {
  const why = ruleIssue(rule);
  const shown = why && !ruleUnfinished(why) ? why : null;
  const whyId = useId();
  const ops = FIELD_OPS[rule.field] as readonly string[];
  return (
    <li className={`rule${shown ? " is-bad" : ""}`}>
      <Select className="sel--float" label={`Rule ${n} field`} value={rule.field} onChange={(e) => onChange(newRule(e.target.value as RuleField))}>
        {RULE_FIELDS.map((f) => (
          <option key={f} value={f}>
            {FIELD_LABELS[f]}
          </option>
        ))}
      </Select>
      {ops.length > 1 ? (
        <Select className="sel--float" label={`Rule ${n} operator`} value={rule.op} onChange={(e) => onChange(withOp(rule, e.target.value))}>
          {ops.map((o) => (
            <option key={o} value={o}>
              {OP_LABELS[o]}
            </option>
          ))}
        </Select>
      ) : (
        <span className="rule-op">{OP_LABELS[rule.op]}</span>
      )}
      <div className="rule-value" aria-describedby={shown ? whyId : undefined}>
        <RuleValue n={n} rule={rule} sessions={sessions} onChange={onChange} invalid={Boolean(shown)} />
      </div>
      {rule.field === "content" ? (
        <button
          type="button"
          className="rule-case"
          aria-pressed={rule.case_sensitive}
          aria-label={`Rule ${n}: case sensitive`}
          title={rule.case_sensitive ? "Case sensitive" : "Not case sensitive"}
          onClick={() => onChange({ ...rule, case_sensitive: !rule.case_sensitive })}
        >
          Aa
        </button>
      ) : (
        <span />
      )}
      <button type="button" className="rule-x" aria-label={`Remove rule ${n}`} title="Remove" onClick={onRemove}>
        ×
      </button>
      {shown && (
        <p className="rule-why" id={whyId} role="alert">
          {shown}
        </p>
      )}
    </li>
  );
}

function RuleValue({
  n,
  rule,
  sessions,
  onChange,
  invalid,
}: {
  n: number;
  rule: Rule;
  sessions: SessionRow[] | null;
  onChange: (r: Rule) => void;
  invalid: boolean;
}) {
  switch (rule.field) {
    case "content":
      return (
        <input
          className={rule.op === "regex" ? "mono" : undefined}
          value={rule.value}
          placeholder={rule.op === "regex" ? "pattern" : "text"}
          aria-label={`Rule ${n} ${rule.op === "regex" ? "pattern" : "text"}`}
          aria-invalid={invalid || undefined}
          spellCheck={rule.op !== "regex"}
          onChange={(e) => onChange({ ...rule, value: e.target.value })}
        />
      );
    case "kind":
      return (
        <div className="chips" role="group" aria-label={`Rule ${n} record kinds`}>
          {RECORD_KINDS.map((k) => {
            const on = rule.value.includes(k);
            return (
              <button
                key={k}
                type="button"
                className={`chip-t${on ? " on" : ""}`}
                aria-pressed={on}
                onClick={() => onChange({ ...rule, value: on ? rule.value.filter((x) => x !== k) : [...rule.value, k] })}
              >
                {k}
              </button>
            );
          })}
        </div>
      );
    case "agent":
    case "session": {
      const options =
        rule.field === "agent"
          ? [...new Set((sessions ?? []).map((s) => s.who))].map((who) => ({ value: who, label: who }))
          : (sessions ?? []).map((s) => ({ value: s.id, label: s.title }));
      return (
        <ChipPicker
          label={`Rule ${n} ${rule.field}s`}
          noun={rule.field}
          options={options}
          loading={sessions === null}
          value={rule.value}
          onChange={(value) => onChange({ ...rule, value })}
        />
      );
    }
    case "time":
      return Array.isArray(rule.value) ? (
        <div className="times">
          <TimeInput label={`Rule ${n} from`} value={rule.value[0] ?? ""} onChange={(v) => onChange({ ...rule, value: [v, (rule.value as string[])[1] ?? ""] })} />
          <TimeInput label={`Rule ${n} to`} value={rule.value[1] ?? ""} onChange={(v) => onChange({ ...rule, value: [(rule.value as string[])[0] ?? "", v] })} />
        </div>
      ) : (
        <TimeInput label={`Rule ${n} time`} value={rule.value} onChange={(value) => onChange({ ...rule, value })} />
      );
    case "length":
      return (
        <span className="with-unit">
          <NumberInput label={`Rule ${n} characters`} value={rule.value} min={0} step={1} onChange={(value) => onChange({ ...rule, value })} />
          <span className="unit">chars</span>
        </span>
      );
  }
}

/** A time in UTC, to the second, through the browser's own date and time box. */
function TimeInput({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <span className="with-unit">
      <input
        type="datetime-local"
        step={1}
        aria-label={`${label}, UTC`}
        value={value.replace(/Z$/, "")}
        onChange={(e) => {
          const v = e.target.value;
          onChange(v ? `${v.length === 16 ? `${v}:00` : v}Z` : "");
        }}
      />
      <span className="unit">UTC</span>
    </span>
  );
}

/** Chosen values as chips, and a list to add one more. */
function ChipPicker({
  label,
  noun,
  options,
  loading,
  value,
  onChange,
}: {
  label: string;
  noun: string;
  options: { value: string; label: string }[];
  loading: boolean;
  value: string[];
  onChange: (v: string[]) => void;
}) {
  const name = (v: string) => options.find((o) => o.value === v)?.label ?? v;
  const rest = options.filter((o) => !value.includes(o.value));
  return (
    <div className="chips" role="group" aria-label={label}>
      {value.map((v) => {
        const known = options.some((o) => o.value === v);
        return (
          <span key={v} className={`chip-on${known || loading ? "" : " is-bad"}`} title={known || loading ? v : `${v}: not in this log source`}>
            <span className="chip-text">{name(v)}</span>
            <button type="button" aria-label={`Remove ${name(v)}`} onClick={() => onChange(value.filter((x) => x !== v))}>
              ×
            </button>
          </span>
        );
      })}
      {loading ? (
        <span className="unit">loading…</span>
      ) : rest.length > 0 ? (
        <Select className="sel--float sel--add" label={`Add ${noun}`} value="" onChange={(e) => e.target.value && onChange([...value, e.target.value])}>
          <option value="">{`add ${noun}`}</option>
          {rest.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </Select>
      ) : (
        !value.length && <span className="unit">{`no ${noun}s in this log source`}</span>
      )}
    </div>
  );
}

/** "Keeps 9 of 28 records · 3 sessions", from the sample logs. */
function SampleCount({ plan, node }: { plan: Plan; node: FilterLogs }) {
  const r = sampleCount(plan, node);
  if ("why" in r && r.why === "the rules are not finished") return null;
  return (
    <p className="count" aria-live="polite">
      {"why" in r ? (
        <span title={`Not counted here: ${r.why}`}>Decided in the box</span>
      ) : (
        <>
          <span className="count-n">
            Keeps {r.kept} of {r.of} record{r.of === 1 ? "" : "s"}
          </span>{" "}
          · {r.sessions} session{r.sessions === 1 ? "" : "s"}
          <span className="count-src" title="Synthetic records in the builder; real records are filtered in the box">
            sample logs
          </span>
        </>
      )}
    </p>
  );
}

function LlmForm({ node, config: c, set }: { node: FilterLogs; config: FilterLlm; set: Set }) {
  const put = (patch: Partial<FilterLlm["llm"]>) => set({ ...node, config: { ...c, llm: { ...c.llm, ...patch } } });
  const judge = useId();
  const unsure = useId();
  const l = c.llm;
  return (
    <>
      <label className="f">
        <span className="f-label">Question</span>
        <textarea
          rows={3}
          value={l.question}
          placeholder="Does this record contain a credential?"
          onChange={(e) => put({ question: e.target.value })}
        />
      </label>
      <div className="f">
        <span className="f-label" id={judge}>
          Judge each
        </span>
        <Segmented
          label="Judge each"
          value={l.judge}
          options={[
            { value: "record", label: "Record" },
            { value: "session", label: "Session" },
          ]}
          onChange={(v) => put({ judge: v })}
        />
      </div>
      <p className="count">Decided in the box</p>
      <details className="f">
        <summary className="f-label">More settings</summary>
        <div className="insp-more">
          <label className="f">
            <span className="f-label">Model</span>
            <Select label="Model" value={l.model.id} onChange={(e) => put({ model: { ...l.model, id: e.target.value } })}>
              {!MODEL_OPTIONS.includes(l.model.id) && <option value={l.model.id}>{l.model.id}</option>}
              {MODEL_OPTIONS.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </Select>
          </label>
          <label className="f">
            <span className="f-label">Yes examples</span>
            <LinesInput value={l.yes_examples} onChange={(yes_examples) => put({ yes_examples })} />
            <span className="f-hint">One per line.</span>
          </label>
          <label className="f">
            <span className="f-label">No examples</span>
            <LinesInput value={l.no_examples} onChange={(no_examples) => put({ no_examples })} />
          </label>
          <div className="f">
            <span className="f-label">Confidence threshold</span>
            <span className="inline-check">
              <input
                type="checkbox"
                aria-label="Use a confidence threshold"
                checked={l.threshold !== undefined}
                onChange={(e) => put({ threshold: e.target.checked ? 0.7 : undefined })}
              />
              {l.threshold !== undefined && (
                <NumberInput label="Confidence threshold" value={l.threshold} min={0} max={1} step={0.05} onChange={(threshold) => put({ threshold })} />
              )}
            </span>
          </div>
          {l.threshold !== undefined && (
            <div className="f">
              <span className="f-label" id={unsure}>
                When unsure
              </span>
              <Segmented
                label="When unsure"
                value={l.unsure}
                options={[
                  { value: "drop", label: "Drop" },
                  { value: "keep", label: "Keep" },
                ]}
                onChange={(v) => put({ unsure: v })}
              />
            </div>
          )}
        </div>
      </details>
    </>
  );
}

const DEFAULT_TITLE = /^(AND|OR)( \d+)?$/;

export function CombineForm({ plan, node, set, op }: { plan: Plan; node: CombineLogs; set: Set; op: Op }) {
  const toggle = (v: "and" | "or") => {
    const title = node.title && DEFAULT_TITLE.test(node.title) ? node.title.replace(/^(AND|OR)/, v.toUpperCase()) : node.title;
    set({ ...node, title, config: { op: v } });
  };
  return (
    <>
      <Segmented
        className="seg--mode"
        label="Combine"
        value={node.config.op}
        options={[
          { value: "and", label: "AND" },
          { value: "or", label: "OR" },
        ]}
        onChange={toggle}
      />
      <div className="f">
        <span className="f-label">Reads from</span>
        <PortPicker plan={plan} node={node} port="a" label="A" op={op} />
        <PortPicker plan={plan} node={node} port="b" label="B" op={op} />
      </div>
    </>
  );
}

/**
 * Which block one fixed input reads, chosen from a list: the keyboard's way to wire it.
 * Only blocks whose records it accepts and that would not close a cycle are offered.
 */
function PortPicker({
  plan,
  node,
  port,
  label,
  op,
}: {
  plan: Plan;
  node: FilterLogs | CombineLogs;
  port: string;
  label: string;
  op: Op;
}) {
  const candidates = plan.nodes.filter((s) => {
    const out = outputPort(s);
    return out && s.id !== node.id && accepts(node.type, out.type) && !wouldCycle(plan, s.id, node.id);
  });
  const title = (id: string) => plan.nodes.find((n) => n.id === id)?.title || id;
  const current = node.inputs[port] ?? "";
  const select = (
    <Select
      label={label}
      value={current}
      onChange={(e) => {
        const v = e.target.value;
        if (!v) return op({ ok: true, plan: disconnect(plan, node.id, port) });
        op(connect(plan, { source: v.split(".")[0], target: node.id, targetHandle: port }));
      }}
    >
      <option value="">nothing</option>
      {current && !candidates.some((s) => `${s.id}.records` === current) && (
        <option value={current}>{title(current.split(".")[0])}</option>
      )}
      {candidates.map((s) => (
        <option key={s.id} value={`${s.id}.${outputPort(s)!.name}`}>
          {title(s.id)}
        </option>
      ))}
    </Select>
  );
  if (node.type === "filter_logs")
    return (
      <div className="f">
        <span className="f-label">{label}</span>
        {select}
      </div>
    );
  return (
    <div className="port-row">
      <span className="port-k" aria-hidden="true">
        {label}
      </span>
      {select}
    </div>
  );
}
