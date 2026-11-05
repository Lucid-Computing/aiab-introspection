import {
  ChallengeHistory,
  ReceiptVerifier,
  ReleasedCards,
  ReleasedTimeline,
} from "./ResultTools";
import { api } from "./api";
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

function EvidenceReference({ run, eventId }: { run: Run; eventId: string }) {
  const [event, setEvent] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  if (run.mode !== "rehearse") return <code>{eventId}</code>;
  return <div><button disabled={loading} onClick={async () => {
    setLoading(true); setError("");
    try { setEvent((await api.evidence(String(run.run_id ?? run.id), eventId)).event); }
    catch (e) { setError((e as Error).message); }
    finally { setLoading(false); }
  }}>{loading ? "Loading source…" : eventId}</button>
    {error && <p role="alert">{error}</p>}
    {event && <article className="wb-card"><strong>Synthetic source record</strong>
      <p>{String(event.source ?? "")} · {String(event.actor ?? "Unknown actor")} · {String(event.occurred_at ?? "Unknown time")}</p>
      <p>{String(event.content ?? "No text content")}</p>
      <JsonDetails title="Authorized fields and provenance" value={event} />
    </article>}
  </div>;
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
              {r.allow_evidence
                ? "agreement permits; current API does not deliver references"
                : "excluded"}
            </li>
            <li>
              Excerpts:{" "}
              {r.allow_excerpts
                ? "agreement permits; current API does not deliver excerpts"
                : "excluded"}
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
  const [releaseMessage, setReleaseMessage] = useState("");
  const [releasing, setReleasing] = useState(false);
  if (!run)
    return (
      <>
        <ReceiptVerifier run={null} />
        <section className="wb-empty">
          <span className="wb-kicker">Execution</span>
          <h2>Test the method, then read the evidence.</h2>
          <p>
            Check the plan, choose an authorized dataset, and rehearse or run.
            Findings here come only from the product API. No model result has
            been generated locally.
          </p>
        </section>
      </>
    );
  const result = run.result ?? run;
  const rawFindings = run.result?.findings ?? run.findings;
  const findings = Array.isArray(rawFindings) ? rawFindings : [];
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
        <strong>
          {run.mode === "rehearse"
            ? "Synthetic rehearsal · fictional evidence · not a consented audit"
            : run.receipt
              ? "Actual audit · signed recipient package (verify independently below)"
              : run.mode === "audit"
                ? "Actual audit request · no signed recipient package available"
                : "Execution origin not supplied"}
        </strong>
        {result.status && result.status !== run.status && (
          <p>Method result: {outcomeLabel(String(result.status))}</p>
        )}
        <p>
          {suppressed
            ? "The release policy withheld this result. Suppression is not a zero count or a clean audit."
            : run.status === "blocked"
              ? "Admission was refused. This run establishes no finding."
              : ["failed", "interrupted", "cancelled", "canceled"].includes(
                    run.status,
                  )
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
          {run.mode === "audit" && !runActive(run) && (
            <button
              disabled={busy || releasing || !hasRunId}
              onClick={() =>
                void (async () => {
                  setReleasing(true);
                  setReleaseMessage("");
                  try {
                    await api.release(String(run.run_id ?? run.id));
                    setReleaseMessage(
                      "Recipient package retrieved after authorization checks.",
                    );
                    onRefresh();
                  } catch (e) {
                    setReleaseMessage(
                      `Release unavailable: ${(e as Error).message}`,
                    );
                  } finally {
                    setReleasing(false);
                  }
                })()
              }
            >
              Retrieve my authorized package
            </button>
          )}
          <button disabled={busy || !hasRunId} onClick={onRefresh}>
            Refresh status
          </button>
        </div>
        {run.mode === "audit" && (
          <p className="wb-muted">
            Cancellation, failure and suppression do not refund an admitted
            audit's reserved release capacity.
          </p>
        )}
        <p role="status">{releaseMessage}</p>
        {run.error != null && (
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
              <div className="wb-grid2 wb-evidence-cards">
                <div>
                  <h4>Supporting evidence</h4>
                  {f.evidence_ids?.length ? (
                    <ul>
                      {f.evidence_ids.map((id) => (
                        <li key={id}>
                          <EvidenceReference run={run} eventId={id} />
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
                          <EvidenceReference run={run} eventId={id} />
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
        <ReleasedCards
          title="Coverage"
          value={result.coverage ?? run.coverage}
        />
        <JsonDetails
          title="Coverage field names & supplied values"
          value={result.coverage ?? run.coverage}
        />
        {!suppressed && (
          <ReleasedCards
            title="Released outcome counts"
            value={result.counts}
          />
        )}
        {suppressed && (
          <p>
            Counts withheld, never zero. No complementary totals are
            reconstructed.
          </p>
        )}
        {!suppressed && (
          <ReleasedCards title="Cohort analysis" value={result.cohorts} />
        )}
        <JsonDetails
          title="Released accounting details"
          value={result.usage ?? run.usage}
        />
      </section>
      {!suppressed && (
        <>
          <ReleasedTimeline value={result.timeline} />
          <ReleasedCards
            title="Artifact lineage · released links"
            value={result.lineage}
          />
          <details>
            <summary>Authorized execution inspection</summary>
            <p>
              Only supplied recipient-authorized material is shown. No private
              trace or evidence endpoint is requested.
            </p>
            <JsonDetails
              title="Released tool events and step details"
              value={result.trace}
            />
          </details>
        </>
      )}
      <ReceiptVerifier run={run} />
      <ChallengeHistory run={run} onChallenge={onChallenge} busy={busy} />
      <section id="wb-challenge">
        <h3>Challenge or supply a correction</h3>
        <p>
          The original report remains immutable. Actual audit method changes require renewed approval. The server authenticates submissions and enforces recipient and reviewer conflict rules. Notifications, deadlines and attempt limits are not implemented.
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
          <fieldset
            disabled={busy || runActive(run) || !hasRunId || !findings.length}
          >
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
              help="Only owners may attach scoped evidence IDs. Recipient evidence delivery is unsupported; leave empty unless you own these authorized artifacts."
            />
            <TextField
              label="Requested remedy"
              value={remedy}
              onChange={setRemedy}
            />
            <TextField
              label="Permitted recipient IDs"
              help={run.mode === "rehearse" ? "Synthetic challenges require only your own authenticated account ID." : "Explicit agreement-authorized principals; the service checks access."}
              value={recipients}
              onChange={setRecipients}
            />
            <button
              type="submit"
              disabled={
                !findings.some((f) => f.id === claim) ||
                !correction.trim() ||
                !remedy.trim() ||
                !recipients.trim()
              }
            >
              Submit challenge
            </button>
          </fieldset>
        </form>
        {!findings.length && (
          <p>
            No released finding is available to challenge in this view. Existing
            authorized challenges can still be loaded above.
          </p>
        )}
        {sent && (
          <p role="status">
            {run.mode === "rehearse" ? "Synthetic challenge stored, owner-only and unverified. No governance disposition is available." : "Challenge stored. An authorized independent reviewer may submit a disposition; the original report remains unchanged."}
          </p>
        )}
        {run.mode === "rehearse" && (
          <p>
            Synthetic challenges are durable, owner-only and unverified; no
            governance disposition or receipt is created.
          </p>
        )}
      </section>
    </div>
  );
}
