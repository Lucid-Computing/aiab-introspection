import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { Field, TextField } from "./Editor";
import { csv, isObject, pretty } from "./model";
import { verifyPackage } from "./receipt";
import type { Run } from "./types";

export function ReceiptVerifier({ run }: { run: Run | null }) {
  const [key, setKey] = useState("");
  const [external, setExternal] = useState("");
  const [expected, setExpected] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const context = useRef("");
  context.current = pretty({
    key,
    external,
    expected,
    projection: run?.result,
    receipt: run?.receipt,
  });
  useEffect(() => setStatus(""), [run, key, external, expected]);
  return (
    <section>
      <h3>Independent receipt verification</h3>
      <p>
        Paste an Ed25519 public key obtained through an independent trusted
        channel. This verifier never fetches or trusts the issuer's own key.
        Verification authenticates issuer, bytes and supplied bindings, not
        factual correctness or protected execution.
      </p>
      <fieldset disabled={busy}>
        <TextField
          label="Independently pinned public key (base64 raw 32 bytes)"
          value={key}
          onChange={setKey}
        />
        <details>
          <summary>Verify an exported package without run access</summary>
          <TextField
            label="Exported package JSON (projection and receipt)"
            multiline
            value={external}
            onChange={setExternal}
          />
        </details>
        <TextField
          label="Expected receipt bindings JSON (optional)"
          value={expected}
          onChange={setExpected}
          help="For example recipient, plan_digest, binding_digest, run_id. Receipt run_id is the governance ID, not the UI run ID. Without independently expected bindings, verification only checks the supplied package."
        />
        <button
          disabled={busy || !key.trim() || (!external.trim() && !run?.receipt)}
          onClick={() =>
            (async () => {
              const verifying = context.current;
              setBusy(true);
              setStatus("");
              try {
                const bindings: unknown = expected.trim()
                  ? JSON.parse(expected)
                  : {};
                if (!isObject(bindings))
                  throw new Error("Expected bindings must be a JSON object.");
                await verifyPackage(
                  external.trim()
                    ? JSON.parse(external)
                    : { projection: run?.result, receipt: run?.receipt },
                  key,
                  bindings,
                );
                if (context.current !== verifying) return;
                setStatus(
                  "Verified signature, projection bytes and supplied expected bindings with your pinned key.",
                );
              } catch (e) {
                if (context.current === verifying)
                  setStatus(`Not verified: ${(e as Error).message}`);
              } finally {
                setBusy(false);
              }
            })()
          }
        >
          Verify with pinned key
        </button>
        <p role="status">{status}</p>
      </fieldset>
      {run?.receipt !== undefined && (
        <details>
          <summary>Signed receipt & exportable recipient package</summary>
          <pre>{pretty({ projection: run.result, receipt: run.receipt })}</pre>
        </details>
      )}
    </section>
  );
}

/** Render only already-released values, without resolving IDs to private resources. */
export function ReleasedCards({
  title,
  value,
}: {
  title: string;
  value: unknown;
}) {
  if (value === undefined || value === null) return null;
  const rows = Array.isArray(value) ? value : [value];
  return (
    <section>
      <h3>{title}</h3>
      {rows.length === 0 ? (
        <p>No entries supplied.</p>
      ) : (
        rows.map((row, i) => (
          <article className="wb-finding" key={i}>
            {isObject(row) ? (
              <dl>
                {Object.entries(row).map(([k, v]) => (
                  <div key={k}>
                    <dt>{k.replaceAll("_", " ")}</dt>
                    <dd>
                      {v === null ? (
                        "Withheld / unavailable"
                      ) : isObject(v) || Array.isArray(v) ? (
                        <ReleasedValues value={v} />
                      ) : (
                        String(v)
                      )}
                    </dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p>{String(row)}</p>
            )}
          </article>
        ))
      )}
    </section>
  );
}
function ReleasedValues({ value }: { value: unknown }) {
  if (Array.isArray(value))
    return (
      <ul>
        {value.map((v, i) => (
          <li key={i}>
            <ReleasedValues value={v} />
          </li>
        ))}
      </ul>
    );
  if (isObject(value))
    return (
      <dl>
        {Object.entries(value).map(([k, v]) => (
          <div key={k}>
            <dt>{k.replaceAll("_", " ")}</dt>
            <dd>
              <ReleasedValues value={v} />
            </dd>
          </div>
        ))}
      </dl>
    );
  return <>{value === null ? "Withheld / unavailable" : String(value)}</>;
}

export function ChallengeHistory({
  run,
  onChallenge,
  busy,
}: {
  run: Run;
  onChallenge: (body: unknown) => Promise<void>;
  busy: boolean;
}) {
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [status, setStatus] = useState("");
  const [parent, setParent] = useState("");
  const [disposition, setDisposition] = useState("unresolved");
  const [correction, setCorrection] = useState("");
  const [recipients, setRecipients] = useState("");
  const [linked, setLinked] = useState("");
  const [working, setWorking] = useState(false);
  const runId = String(run.run_id ?? run.id ?? "");
  const load = async () => {
    setWorking(true);
    setStatus("");
    try {
      setRows((await api.challenges(runId)).challenges);
    } catch (e) {
      setStatus(`Challenge history unavailable: ${(e as Error).message}`);
    } finally {
      setWorking(false);
    }
  };
  const selected = rows.find((r) => r.id === parent);
  return (
    <section>
      <h3>Authorized challenge history</h3>
      <p>
        Original findings and receipts remain immutable. History includes only
        records authorized for your account. Delivery notifications, deadlines
        and attempt limits are not implemented.
      </p>
      <button disabled={!runId || busy || working} onClick={() => void load()}>
        Load challenge history
      </button>
      {rows.map((row) => (
        <article className="wb-note" key={String(row.id)}>
          <strong>{String(row.disposition ?? "Challenge")}</strong>
          <p>
            {String(
              row.correction ??
                "Correction text not available; commitment retained.",
            )}
          </p>
          <dl>
            {[
              "id",
              "claim_id",
              "actor",
              "parent_id",
              "requested_remedy",
              "correction_commitment",
              "corrected_release_id",
              "verification",
            ]
              .filter((k) => row[k] != null)
              .map((k) => (
                <div key={k}>
                  <dt>{k.replaceAll("_", " ")}</dt>
                  <dd>{String(row[k])}</dd>
                </div>
              ))}
          </dl>
        </article>
      ))}
      {run.mode === "audit" && rows.length > 0 && (
        <details>
          <summary>Request a reviewer disposition</summary>
          <p>
            Requires an independently enrolled challenge reviewer, inclusion in
            the parent challenge, no owner/challenger conflict, and narrowed
            recipients. This page cannot establish your role; the service
            authorizes every submission. Linked corrected releases must already
            satisfy ordinary release rules.
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              return (async () => {
                if (!selected) return;
                setWorking(true);
                setStatus("");
                try {
                  await onChallenge({
                    finding_id: selected.claim_id,
                    correction,
                    evidence_ids: [],
                    requested_remedy: String(selected.requested_remedy),
                    recipients: csv(recipients),
                    parent_id: parent,
                    disposition,
                    ...(linked.trim()
                      ? { corrected_release_id: linked.trim() }
                      : {}),
                  });
                  setCorrection("");
                  await load();
                  setStatus("Disposition stored by the service.");
                } catch (e) {
                  setStatus((e as Error).message);
                } finally {
                  setWorking(false);
                }
              })();
            }}
          >
            <fieldset disabled={busy || working}>
              <Field label="Parent challenge">
                <select
                  value={parent}
                  onChange={(e) => {
                    setParent(e.target.value);
                    setRecipients("");
                  }}
                >
                  <option value="">Choose an authorized challenge</option>
                  {rows.map((r) => (
                    <option key={String(r.id)} value={String(r.id)}>
                      {String(r.id)}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Disposition">
                <select
                  value={disposition}
                  onChange={(e) => setDisposition(e.target.value)}
                >
                  {["upheld", "amended", "withdrawn", "unresolved"].map((d) => (
                    <option key={d}>{d}</option>
                  ))}
                </select>
              </Field>
              <TextField
                label="Disposition explanation / correction"
                multiline
                value={correction}
                onChange={setCorrection}
              />
              <TextField
                label="Narrowed authorized recipients"
                value={recipients}
                onChange={setRecipients}
                help={
                  selected && Array.isArray(selected.recipients)
                    ? `Parent recipients: ${selected.recipients.join(", ")}`
                    : "Choose a parent first."
                }
              />
              <TextField
                label="Existing corrected release ID (optional)"
                value={linked}
                onChange={setLinked}
              />
              <button
                disabled={!selected || !correction.trim() || !recipients.trim()}
              >
                Submit authorized disposition request
              </button>
            </fieldset>
          </form>
        </details>
      )}
      <p role="status">{status}</p>
    </section>
  );
}

export function ReleasedTimeline({ value }: { value: unknown }) {
  if (value == null) return null;
  if (!Array.isArray(value))
    return <ReleasedCards title="Timeline · released events" value={value} />;
  return (
    <section>
      <h3>Timeline · released events</h3>
      <p>Service-supplied order; missing times remain unspecified.</p>
      <ol className="wb-timeline">
        {value.map((entry, i) => (
          <li key={i}>
            {isObject(entry) ? (
              <>
                <strong>
                  {String(
                    entry.title ??
                      entry.event ??
                      entry.kind ??
                      "Released event",
                  )}
                </strong>
                {(typeof entry.time === "string" ||
                  typeof entry.timestamp === "string") && (
                  <p>
                    <time>{String(entry.time ?? entry.timestamp)}</time>
                  </p>
                )}
                <ReleasedValues value={entry} />
              </>
            ) : (
              <p>{String(entry)}</p>
            )}
          </li>
        ))}
      </ol>
      {!value.length && <p>No events supplied.</p>}
    </section>
  );
}
