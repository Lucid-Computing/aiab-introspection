import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { csv } from "./model";
import { Field, TextField } from "./Editor";
import type { Plan, SavedPlan } from "./types";

export default function Reviews({
  plan,
  saved,
  dirty,
  online,
  onUnsaved,
}: {
  plan: Plan;
  saved: SavedPlan | null;
  dirty: boolean;
  online: boolean;
  onUnsaved: (value: boolean) => void;
}) {
  const [noteEpoch, setNoteEpoch] = useState(0);
  const [text, setText] = useState("");
  const [noteTarget, setNoteTarget] = useState("");
  const [clause, setClause] = useState("/question");
  const [recipients, setRecipients] = useState("");
  const [revision, setRevision] = useState("");
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const digest = revision.trim() || saved?.digest || "";
  const currentTarget = `${plan.id}:${saved?.digest ?? "unsigned"}`;
  const staleTarget = !!text.trim() && noteTarget !== currentTarget;
  const context = useRef("");
  context.current = `${plan.id}:${digest}`;
  useEffect(() => {
    onUnsaved(!!text.trim());
  }, [text, onUnsaved]);
  useEffect(() => {
    setRows([]);
    setMessage("");
  }, [saved?.digest, plan.id, revision]);
  const execute = async (task: () => Promise<void>) => {
    setBusy(true);
    setMessage("");
    try {
      await task();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section>
      <h3>Review notes</h3>
      <fieldset disabled={busy}>
        <p>
          Durable notes bind an exact saved revision and clause. Author-only
          unless you explicitly share. Notes never grant signing consent.
        </p>
        <TextField
          label="Historical revision digest (optional)"
          value={revision}
          onChange={setRevision}
          help="Leave empty for the current saved revision. Historical access is checked by the service."
        />
        <button
          disabled={!online || !saved || !digest || busy}
          onClick={() =>
            void execute(async () => {
              const requested = context.current;
              const result = await api.reviews(plan.id, digest);
              if (context.current !== requested) return;
              setRows(result.reviews);
              setMessage("Loaded authorized notes for the requested revision.");
            })
          }
        >
          Load revision notes
        </button>
        {rows.map((n) => (
          <article className="wb-note" key={String(n.id)}>
            <small>
              {String(n.actor)} · {String(n.clause)}
            </small>
            <p>{String(n.note)}</p>
            <small>
              Revision {String(n.plan_digest)} · clause commitment{" "}
              {String(n.clause_digest)}
            </small>
            <p>
              Visible to:{" "}
              {Array.isArray(n.recipients)
                ? n.recipients.join(", ")
                : "not supplied"}
            </p>
          </article>
        ))}
        <Field label="Exact clause">
          <select value={clause} onChange={(e) => setClause(e.target.value)}>
            {[
              "question",
              "criteria",
              "scope",
              "steps",
              "agent",
              "disclosure",
              "approval",
            ].map((k) => (
              <option key={k} value={`/${k}`}>
                {k}
              </option>
            ))}
            {plan.criteria.map((c, i) => (
              <option key={`criterion-${c.id}`} value={`/criteria/${i}`}>
                Criterion: {c.description}
              </option>
            ))}
            {plan.disclosure.recipients.map((r, i) => (
              <option
                key={`recipient-${i}`}
                value={`/disclosure/recipients/${i}`}
              >
                Recipient: {r.id}
              </option>
            ))}
            {plan.steps.map((s, i) => (
              <option key={s.id} value={`/steps/${i}`}>
                {s.title}
              </option>
            ))}
          </select>
        </Field>
        <TextField
          key={noteEpoch}
          label="Question or comment"
          multiline
          value={text}
          onChange={(value) => {
            if (!text.trim()) setNoteTarget(currentTarget);
            setText(value);
          }}
        />
        <TextField
          label="Share with authorized recipient IDs (optional)"
          value={recipients}
          onChange={setRecipients}
          help="Empty means author-only. The service checks recipients against the agreement."
        />
        {text.trim() && (
          <p role="status">
            Unsaved note · held only in this page until the service confirms
            storage.
          </p>
        )}
        {staleTarget && (
          <div className="wb-notice">
            <p>
              This unsaved note began on {noteTarget}. The displayed saved
              revision changed. Review its clause and explicitly retarget before
              storing.
            </p>
            <button
              disabled={!saved || dirty}
              onClick={() => setNoteTarget(currentTarget)}
            >
              Use displayed saved revision for this unsaved note
            </button>
          </div>
        )}
        {(dirty || !saved) && (
          <p>
            Save and review this exact agreement before storing a note. Draft
            text remains unsaved.
          </p>
        )}
        <button
          disabled={
            !online ||
            busy ||
            !saved ||
            dirty ||
            staleTarget ||
            !!revision.trim() ||
            !text.trim()
          }
          onClick={() =>
            void execute(async () => {
              if (!saved) return;
              const requested = context.current;
              const note = await api.review(plan.id, {
                digest: saved.digest,
                clause,
                note: text,
                ...(recipients.trim() ? { recipients: csv(recipients) } : {}),
              });
              if (context.current !== requested) return;
              setRows((prev) => [...prev, note]);
              setText("");
              setNoteEpoch((e) => e + 1);
              setMessage(
                "Note stored for this exact revision. This is not approval.",
              );
            })
          }
        >
          Store exact-version note
        </button>
        <p role="status">{message}</p>
      </fieldset>
    </section>
  );
}
