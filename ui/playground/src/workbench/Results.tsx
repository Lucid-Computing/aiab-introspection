import { useState } from "react";
import { Field, TextField } from "./Editor";
import { outcomeLabel, pretty, runActive, csv } from "./model";
import type { Plan, Run } from "./types";

export function JsonDetails({
  title,
  value,
  open = false,
}: {
  title: string;
  value: unknown;
  open?: boolean;
}) {
  return (
    <details open={open} className="wb-data">
      <summary>{title}</summary>
      <pre>{pretty(value)}</pre>
    </details>
  );
}
export function RecipientPreview({ plan }: { plan: Plan }) {
  const [index, setIndex] = useState(0);
  const r = plan.disclosure.recipients[index] ?? plan.disclosure.recipients[0];
  return (
    <section className="wb-recipient">
      <h3>Recipient preview</h3>
      <p className="wb-muted">
        Policy preview · no private result is loaded or projected in the
        browser.
      </p>
      <Field label="View agreement as">
        <select
          value={Math.min(
            index,
            Math.max(0, plan.disclosure.recipients.length - 1),
          )}
          onChange={(e) => setIndex(Number(e.target.value))}
        >
          {plan.disclosure.recipients.map((x, i) => (
            <option key={i} value={i}>
              {x.id || "Unnamed"} · {x.role}
            </option>
          ))}
        </select>
      </Field>
      {r ? (
        <>
          <p>
            May receive:{" "}
            <strong>{r.fields.join(", ") || "No fields declared"}</strong>.
          </p>
          <ul>
            <li>
              Evidence references:{" "}
              {r.allow_evidence ? "permitted only when authorized" : "excluded"}
            </li>
            <li>
              Excerpts:{" "}
              {r.allow_excerpts ? "permitted only when approved" : "excluded"}
            </li>
            <li>
              Groups smaller than {plan.disclosure.min_group_size}: suppressed,
              never reported as zero.
            </li>
            <li>
              Release limit: {plan.disclosure.max_releases}. Cumulative policy
              applies.
            </li>
          </ul>
          <div className="wb-example">
            <strong>Example of a permitted shape</strong>
            <pre>
              {pretty(
                Object.fromEntries(
                  r.fields.map((f) => [
                    f,
                    f === "coverage"
                      ? "authorized coverage summary"
                      : "authorized recipient-safe value",
                  ]),
                ),
              )}
            </pre>
            <small>
              Illustrative field names only; this is not an audit result.
            </small>
          </div>
          <p>
            Forbidden: any field outside this agreement, unauthorized raw
            records, or private execution traces.
          </p>
        </>
      ) : (
        <p>No recipient declared.</p>
      )}
    </section>
  );
}
export default function Results({
  run,
  onCancel,
  onRefresh,
  onChallenge,
  busy,
}: {
  run: Run | null;
  onCancel: () => void;
  onRefresh: () => void;
  onChallenge: (body: unknown) => Promise<void>;
  busy: boolean;
}) {
  const [claim, setClaim] = useState("");
  const [correction, setCorrection] = useState("");
  const [remedy, setRemedy] = useState("Review the disputed finding");
  const [recipients, setRecipients] = useState("");
  const [refs, setRefs] = useState("");
  const [sent, setSent] = useState(false);
  if (!run)
    return (
      <section className="wb-empty">
        <span className="wb-kicker">Execution</span>
        <h2>Test the method, then read the evidence.</h2>
        <p>
          Check the plan, choose an authorized dataset, and rehearse or run.
          Findings here come only from the product API. No model result has been
          generated locally.
        </p>
      </section>
    );
  const result = run.result ?? run;
  const findings = run.result?.findings ?? run.findings ?? [];
  const suppressed =
    run.status === "suppressed" ||
    result.status === "suppressed" ||
    run.release_state === "suppressed" ||
    result.release_state === "suppressed";
  const hasRunId = !!(run.run_id ?? run.id);
  return (
    <div className="wb-results">
      <section
        className={`wb-run-banner wb-state-${run.status}`}
        aria-live="polite"
      >
        <span className="wb-kicker">Run {run.run_id ?? run.id ?? ""}</span>
        <h2>{outcomeLabel(run.status)}</h2>
        {result.status && result.status !== run.status && (
          <p>Method result: {outcomeLabel(String(result.status))}</p>
        )}
        <p>
          {suppressed
            ? "The release policy withheld this result. Suppression is not a zero count or a clean audit."
            : run.status === "blocked"
              ? "Admission was refused. This run establishes no finding."
              : run.status === "failed"
                ? "Execution did not complete. A failure is not evidence of compliance."
                : runActive(run)
                  ? "Executing against the saved plan and admitted dataset. Only recipient-safe progress appears here."
                  : "Review each conclusion with its evidence, counterevidence and coverage limitations."}
        </p>
        <div className="wb-actions">
          {runActive(run) && (
            <button disabled={busy || !hasRunId} onClick={onCancel}>
              Cancel run
            </button>
          )}
          <button disabled={busy || !hasRunId} onClick={onRefresh}>
            Refresh status
          </button>
        </div>
        {run.error !== undefined && (
          <JsonDetails
            title="Execution or admission reason"
            value={run.error}
            open
          />
        )}
        {(run.reason !== undefined || run.message !== undefined) && (
          <JsonDetails
            title="Status details"
            value={run.reason ?? run.message}
            open
          />
        )}
      </section>
      <section>
        <h3>Answer & scope</h3>
        {typeof result.summary === "string" ? (
          <p>{result.summary}</p>
        ) : (
          <p>
            No separate answer summary was released. Findings below retain their
            individual outcomes.
          </p>
        )}
        <JsonDetails
          title="Authorized scope & unresolved questions"
          value={{
            scope: run.scope ?? result.scope,
            unresolved_questions: result.unresolved_questions,
            plan_digest: run.plan_digest,
            dataset_id: run.dataset_id,
            mode: run.mode,
          }}
        />
      </section>
      {suppressed ? (
        <section className="wb-empty">
          <h3>Results withheld</h3>
          <p>Findings and counts are suppressed by release policy.</p>
        </section>
      ) : (
        <section>
          <h3>
            Findings{" "}
            <span className="wb-muted">
              / {findings.length} released entries
            </span>
          </h3>
          {!findings.length && (
            <p>
              No findings were released. This alone does not establish a
              negative finding.
            </p>
          )}
          {findings.map((f) => (
            <article key={f.id} className={`wb-finding wb-state-${f.outcome}`}>
              <span className="wb-badge">{outcomeLabel(f.outcome)}</span>
              <h3>{f.title}</h3>
              <p>{f.claim}</p>
              <p className="wb-muted">
                Criterion {f.criterion_id} · method {f.method} ·{" "}
                {String(f.details?.claim_type ?? "claim strength not supplied")}
              </p>
              <div className="wb-grid2">
                <div>
                  <h4>Supporting evidence</h4>
                  {f.evidence_ids?.length ? (
                    <ul>
                      {f.evidence_ids.map((id) => (
                        <li key={id}>
                          <code>{id}</code>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p>No support references released.</p>
                  )}
                </div>
                <div>
                  <h4>Counterevidence</h4>
                  {f.counterevidence_ids?.length ? (
                    <ul>
                      {f.counterevidence_ids.map((id) => (
                        <li key={id}>
                          <code>{id}</code>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p>
                      No counterevidence references released; this does not
                      prove none exists.
                    </p>
                  )}
                </div>
              </div>
              {f.limitations?.length > 0 && (
                <div className="wb-notice">
                  <strong>Limitations</strong>
                  <ul>
                    {f.limitations.map((x, i) => (
                      <li key={i}>{x}</li>
                    ))}
                  </ul>
                </div>
              )}
              <JsonDetails
                title="Released method, coverage & evidence details"
                value={f.details}
              />
              <button
                onClick={() => {
                  setClaim(f.id);
                  setSent(false);
                  document
                    .getElementById("wb-challenge")
                    ?.scrollIntoView({ behavior: "smooth" });
                }}
              >
                Challenge this finding
              </button>
            </article>
          ))}
        </section>
      )}
      <section>
        <h3>Coverage & accounting</h3>
        <p>
          Missing coverage remains unknown. A committed hash does not prove
          source completeness.
        </p>
        <JsonDetails
          title="Population → selection → links → claims → released fields"
          value={result.coverage ?? run.coverage}
          open
        />
        <JsonDetails
          title="Actual usage, cost and budgets"
          value={result.usage ?? run.usage}
        />
      </section>
      <section>
        <h3>Execution inspection</h3>
        <p>
          Only trace material explicitly released by the backend can appear
          here. Full private traces are not requested.
        </p>
        <JsonDetails
          title="Authorized step inputs/outputs, failed joins & tool events"
          value={result.trace}
        />
        <JsonDetails
          title="Timeline, artifact lineage & cohort analysis (when released)"
          value={{
            timeline: result.timeline,
            lineage: result.lineage,
            cohorts: result.cohorts,
          }}
        />
      </section>
      <section>
        <h3>Verification</h3>
        <p>
          These are server-supplied commitments. Their presence alone does not
          independently verify execution or evidence correctness.
        </p>
        <JsonDetails
          title="Plan, dataset, approval, runtime/model & receipt bundle"
          value={{
            plan_digest: run.plan_digest,
            dataset_manifest: run.dataset_manifest,
            approvals: run.approvals,
            runtime: run.runtime ?? result.runtime,
            model: run.model ?? result.model,
            receipt: run.receipt ?? result.receipt,
            verification: run.verification ?? result.verification,
          }}
          open
        />
      </section>
      <section id="wb-challenge">
        <h3>Challenge or supply a correction</h3>
        <p>
          The original report remains immutable. A changed method requires
          renewed approval. The server authenticates submissions and enforces
          recipients, deadlines, attempt limits and conflict rules.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void onChallenge({
              finding_id: claim,
              correction,
              evidence_ids: csv(refs),
              requested_remedy: remedy,
              recipients: csv(recipients),
            })
              .then(() => setSent(true))
              .catch(() => {});
          }}
        >
          <fieldset disabled={busy || runActive(run) || !hasRunId}>
            <TextField
              label="Disputed finding or claim ID"
              value={claim}
              onChange={setClaim}
            />
            <TextField
              label="Correction and supporting explanation"
              multiline
              value={correction}
              onChange={setCorrection}
            />
            <TextField
              label="Authenticated evidence references"
              value={refs}
              onChange={setRefs}
              help="Reference already authorized correction artifacts; pasted text alone is not proof of authenticity."
            />
            <TextField
              label="Requested remedy"
              value={remedy}
              onChange={setRemedy}
            />
            <TextField
              label="Permitted recipient IDs"
              value={recipients}
              onChange={setRecipients}
            />
            <button
              type="submit"
              disabled={!claim.trim() || !correction.trim() || !remedy.trim()}
            >
              Submit challenge
            </button>
          </fieldset>
        </form>
        {sent && (
          <p role="status">
            Challenge submitted. Its disposition is determined by the authorized
            reviewer.
          </p>
        )}
        <JsonDetails
          title="Challenge dispositions & linked amendments"
          value={run.challenges ?? result.challenges}
        />
      </section>
    </div>
  );
}
