import { useEffect, useRef, useState } from "react";
import { api, evaluationActive, pollEvaluation } from "./api";
import { Field, TextField } from "./Editor";
import { pretty } from "./model";
import { JsonDetails } from "./Results";
import type { Evaluation, SavedPlan } from "./types";

export function EvaluationReport({ value }: { value: Evaluation }) {
  const rows = value.report?.cases ?? [];
  const families = [...new Set(rows.map((r) => r.criterion_id))];
  const m = value.metrics;
  const testModel = value.report?.test_model || rows.some((r) => r.usage?.test_model === true);
  return <section className="wb-evaluation-report">
    <h3>Evaluation {value.status}</h3>
    <p className="wb-digest">ID: {value.evaluation_id}</p>
    <p>{value.include_agent ? "Live DeepSeek and plan tools" : "Deterministic derived plan"} · synthetic full trace · seed {value.seed} · cap {value.case_limit}</p>
    {testModel && <p className="wb-notice">TEST MODEL · server fixture, not live DeepSeek</p>}
    {value.error && <p role="alert" className="wb-error">{value.error}</p>}
    <p>Saved plan digest: <code>{value.plan_digest}</code></p>
    <p>Exact evaluated plan digest: <code>{value.evaluated_digest}</code></p>
    <p>These results stay bound to this saved snapshot even when the draft changes.</p>
    <JsonDetails title="Exact evaluated plan" value={value.evaluated_plan} />
    <details><summary>Exact evaluated-plan changes · {value.changes.length}</summary>
      {!value.changes.length && <p>No changes from the saved plan.</p>}
      {value.changes.map((c) => <article className="wb-card" key={c.path}><h4>{c.path}</h4><div className="wb-grid2"><div>Saved<pre>{pretty(c.before)}</pre></div><div>Evaluated<pre>{pretty(c.after)}</pre></div></div></article>)}
    </details>
    <p>Available cases: {value.report?.available_cases ?? "pending"} · Selected: {value.report?.selected_cases ?? "pending"} · Excluded by cap: {value.report?.excluded_by_limit ?? "pending"} · Committed cases: {rows.length}{value.report?.partial ? " (partial)" : ""}</p>
    {m && <>
      <h4>Synthetic supportable-outcome metrics</h4>
      <p>Evaluated {m.evaluated} / {m.cases} selected · Unknown {m.unknown} · Abstentions {m.abstentions} · Citation errors {m.citation_errors}</p>
      <table><thead><tr><th>Metric</th><th>Value</th><th>Denominator</th></tr></thead><tbody>
        {([['Exact outcome match rate', 'accuracy', 'accuracy_denominator'], ['Precision', 'precision', 'precision_denominator'], ['Recall', 'recall', 'recall_denominator']] as const).map(([label, key, denominator]) => <tr key={key}><th>{label}</th><td>{m[key] == null ? "Undefined" : `${(m[key]! * 100).toFixed(1)}%`}</td><td>{m[denominator]}</td></tr>)}
      </tbody></table>
      <p>TP {m.tp} · FP {m.fp} · FN including unknowns and abstentions {m.fn_including_unknown_and_abstention} · TN {m.tn}</p>
    </>}
    <p>Unknown cases have no predicted outcome and remain in the selected-case denominator. Abstention is not a clean negative. This bounded synthetic cohort makes no real-world or model accuracy claim.</p>
    <h4>Per-family execution coverage</h4>
    {value.report?.family_metrics ? <table><thead><tr><th>Scenario family</th><th>Exact match / cases</th><th>Precision / predicted positives</th><th>Recall / supportable positives</th><th>Unknown / abstained</th></tr></thead><tbody>
      {value.report.family_metrics.map((f) => <tr key={`${f.criterion_id}:${f.family}`}><th>{f.criterion_id} · {f.family}</th>
        <td>{(f.accuracy * 100).toFixed(1)}% / {f.accuracy_denominator}</td>
        <td>{f.precision == null ? "Undefined" : `${(f.precision * 100).toFixed(1)}%`} / {f.precision_denominator}</td>
        <td>{f.recall == null ? "Undefined" : `${(f.recall * 100).toFixed(1)}%`} / {f.recall_denominator}</td>
        <td>{f.unknown} / {f.abstentions}</td></tr>)}
    </tbody></table> : <p>Family precision, recall and exact-match metrics are not supplied by this API; hidden labels are unavailable to this page.</p>}
    <table><thead><tr><th>Criterion family</th><th>Evaluated / committed</th><th>Unknown / committed</th><th>Abstentions / committed</th><th>Failures / committed</th></tr></thead><tbody>
      {families.map((family) => { const cases = rows.filter((r) => r.criterion_id === family); return <tr key={family}><th>{family}</th>{[cases.filter((r) => r.status === "evaluated").length, cases.filter((r) => r.status === "unknown").length, cases.filter((r) => r.outcome === "insufficient_evidence").length, cases.filter((r) => r.failure).length].map((n, i) => <td key={i}>{n} / {cases.length}</td>)}</tr>; })}
    </tbody></table>
    <h4>Case outcomes and coverage</h4>
    {rows.map((r) => <article className="wb-card" key={`${r.criterion_id}:${r.investigation_id}`}><strong>{r.criterion_id} · {r.investigation_id}</strong><p>{r.status} · {r.outcome ?? "No predicted outcome"} · {r.observed_events} observed events</p>{r.failure && <p className="wb-error">Failure: {r.failure}</p>}<JsonDetails title="Actual case coverage and runtime usage" value={{ coverage: r.coverage, usage: r.usage }} /></article>)}
    {value.report?.limitations?.map((l) => <p key={l}>{l}</p>)}
  </section>;
}

export default function Evaluations({ saved, eligible, online, account }: { saved: SavedPlan | null; eligible: boolean; online: boolean; account: string }) {
  const [agent, setAgent] = useState(false);
  const [limit, setLimit] = useState("16");
  const [seed, setSeed] = useState("0");
  const [ident, setIdent] = useState("");
  const [openId, setOpenId] = useState("");
  const [known, setKnown] = useState<string[]>([]);
  const [value, setValue] = useState<Evaluation | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [epoch, setEpoch] = useState(0);
  const key = `aiab-workbench-evaluations:${account}`;
  const context = useRef({ key, saved, eligible });
  context.current = { key, saved, eligible };
  useEffect(() => {
    setIdent(""); setValue(null); setError("");
    try { const ids: unknown = JSON.parse(localStorage.getItem(key) ?? "[]"); const list = Array.isArray(ids) ? ids.filter((x): x is string => typeof x === "string") : []; setKnown(list); } catch { setKnown([]); }
  }, [key]);
  useEffect(() => {
    if (!ident || !online) return;
    const controller = new AbortController();
    setError("");
    void pollEvaluation(ident, setValue, controller.signal).catch((e: Error) => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [ident, epoch, online, key]);
  const remember = (id: string) => {
    setKnown((old) => { const ids = [...new Set([id, ...old])]; try { localStorage.setItem(key, JSON.stringify(ids)); } catch { setError("Evaluation created, but browser storage is unavailable. Copy the evaluation ID to reopen it."); } return ids; });
    setValue(null); setIdent(id); setEpoch((n) => n + 1);
  };
  const execute = async (task: () => Promise<void>) => { setBusy(true); setError(""); try { await task(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } };
  const validBounds = Number.isInteger(Number(limit)) && Number(limit) >= 1 && Number(limit) <= (agent ? 4 : 16) && seed.trim() !== "" && Number.isInteger(Number(seed)) && Number(seed) >= 0 && Number(seed) <= 2147483647;
  return <section className="wb-evaluations">
    <h2>Evaluate a saved agreement</h2>
    {!online && <p className="wb-notice">Evaluation service unavailable until the authenticated backend connects.</p>}
    <p>Server-generated synthetic evidence only. Stored or imported evidence is never used. The plan must explicitly scope northstar and catalog criteria; service admission remains authoritative.</p>
    <fieldset disabled={busy}>
      <Field label="Evaluation mode"><select value={agent ? "agent" : "deterministic"} onChange={(e) => { const live = e.target.value === "agent"; setAgent(live); setLimit(live ? "4" : "16"); }}><option value="deterministic">Deterministic derived plan</option><option value="agent">Live DeepSeek and plan tools</option></select></Field>
      <p>{agent ? "Executes the actual agent plan. Per case: at most 180 runtime seconds, 32,000 tokens, 12 turns, 40 tool calls; external deadline 200 seconds. Maximum 4 cases." : "Removes agent steps and their transitive dependants. Exact changes are recorded; incompatible graphs fail admission. Maximum 16 cases."} Job deadline: case cap × 200 + 30 seconds. No fallback execution.</p>
      <TextField label={`Case cap (1–${agent ? 4 : 16})`} value={limit} onChange={setLimit} />
      <TextField label="Fixture seed (0–2147483647)" value={seed} onChange={setSeed} />
      <p>Saved checked digest: <code>{eligible ? saved?.digest : "Save and Check plan for the current draft first."}</code></p>
      <button disabled={!online || !eligible || !saved || !validBounds || !!value && evaluationActive(value)} onClick={() => execute(async () => {
        if (!saved || !eligible) return;
        const expected = context.current;
        const remote = await api.loadPlan(saved.plan.id);
        if (context.current.key !== expected.key || context.current.saved !== saved || !context.current.eligible || remote.digest !== saved.digest || pretty(remote.plan) !== pretty(saved.plan)) throw new Error("Saved agreement changed. Reload, save and check before evaluating.");
        const result = await api.evaluate(saved.plan.id, agent, Number(limit), Number(seed), saved.digest);
        if (!result.evaluation_id) throw new Error("Evaluation API returned no evaluation_id.");
        if (context.current.key === expected.key) remember(result.evaluation_id);
      })}>Start synthetic evaluation</button>
    </fieldset>
    <TextField label="Open evaluation ID" value={openId} onChange={setOpenId} />
    <button disabled={!online || !openId.trim() || busy} onClick={() => remember(openId.trim())}>Open owner-only evaluation</button>
    {!!known.length && <details><summary>Retained evaluation IDs</summary>{known.map((id) => <p key={id}><button disabled={busy || !online} onClick={() => remember(id)}>{id}</button></p>)}</details>}
    {ident && <><p className="wb-digest">Monitoring {ident}</p><button disabled={!online || busy} onClick={() => setEpoch((n) => n + 1)}>Refresh / resume evaluation polling</button>
      {(!value || evaluationActive(value)) && <button disabled={!online || busy} onClick={() => execute(async () => { await api.cancelEvaluation(ident); setEpoch((n) => n + 1); })}>Cancel evaluation</button>}</>}
    {busy && <p role="status">Contacting evaluation service…</p>}
    {error && <p role="alert" className="wb-error">{error}</p>}
    {value && <EvaluationReport value={value} />}
  </section>;
}
