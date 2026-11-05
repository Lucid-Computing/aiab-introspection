import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, pollRun } from "./api";
import Editor, { Field, TextField } from "./Editor";
import {
  blankPlan,
  executionBlockers,
  isObject,
  localIssues,
  parsePlan,
  pretty,
  runActive,
} from "./model";
import Results, { JsonDetails, RecipientPreview } from "./Results";
import { Changes, CodeView, GraphView } from "./Views";
import type {
  Audit,
  Bootstrap,
  Check,
  Dataset,
  Plan,
  Run,
  SavedPlan,
} from "./types";
import "./workbench.css";

type Tab = "document" | "graph" | "code" | "changes" | "results" | "activity";
interface Note {
  id: number;
  clause: string;
  author: string;
  text: string;
  responsibility: string;
  resolved: boolean;
}
function asSaved(value: SavedPlan | Plan): SavedPlan {
  return isObject(value.plan)
    ? (value as SavedPlan)
    : { plan: value as Plan, digest: "" };
}
export default function WorkbenchApp() {
  const [plan, setPlan] = useState<Plan>(blankPlan);
  const [saved, setSaved] = useState<SavedPlan | null>(null);
  const [edited, setEdited] = useState(false);
  const [data, setData] = useState<Bootstrap | null>(null);
  const [online, setOnline] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [tab, setTab] = useState<Tab>("document");
  const [selected, setSelected] = useState("");
  const [search, setSearch] = useState("");
  const [check, setCheck] = useState<{
    snapshot: string;
    result: Check;
  } | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [runId, setRunId] = useState("");
  const [pollEpoch, setPollEpoch] = useState(0);
  const [pollError, setPollError] = useState("");
  const [openRunId, setOpenRunId] = useState("");
  const [history, setHistory] = useState<unknown>(undefined);
  const [importText, setImportText] = useState("");
  const [importError, setImportError] = useState("");
  const [notes, setNotes] = useState<Note[]>([]);
  const [noteText, setNoteText] = useState("");
  const [responsibility, setResponsibility] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const snapshot = pretty(plan);
  const dirty = edited && (!saved || pretty(saved.plan) !== snapshot);
  const currentCheck = check?.snapshot === snapshot ? check.result : null;
  const issues = localIssues(plan);
  const dataset = data?.datasets.find(
    (d) => d.id === plan.scope.dataset_ids[0],
  );
  const active = !!run && runActive(run);

  const bootstrap = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    try {
      const result = await api.bootstrap(signal);
      if (
        !Array.isArray(result.audits) ||
        !Array.isArray(result.datasets) ||
        !Array.isArray(result.plans)
      )
        throw new Error(
          "Bootstrap must include audits, dataset manifests and plans arrays.",
        );
      if (signal?.aborted) return;
      setData(result);
      setOnline(true);
      setError("");
    } catch (e) {
      if (!signal?.aborted) {
        setOnline(false);
        setError((e as Error).message);
      }
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void bootstrap(controller.signal);
    return () => controller.abort();
  }, [bootstrap]);
  useEffect(() => {
    if (!dirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [dirty]);
  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController();
    setPollError("");
    void pollRun(
      runId,
      (value) => setRun({ ...value, run_id: runId }),
      controller.signal,
    ).catch((e: Error) => {
      if (!controller.signal.aborted)
        setPollError(
          `Status unavailable: ${e.message}. The run may still be active.`,
        );
    });
    return () => controller.abort();
  }, [runId, pollEpoch]);

  const update = (next: Plan) => {
    setPlan(next);
    setEdited(true);
    setNotice("");
  };
  const load = (value: SavedPlan) => {
    const next = parsePlan(pretty(value.plan));
    setPlan(next);
    setSaved(value.digest ? { ...value, plan: next } : null);
    setEdited(false);
    setCheck(null);
    setSelected("");
    setNotice(
      value.digest
        ? "Loaded saved agreement. Approvals apply only to its displayed digest."
        : "Loaded an unsigned worked plan. Save and check it before execution.",
    );
    setTab("document");
  };
  async function perform<T>(label: string, task: () => Promise<T>): Promise<T> {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      return await task();
    } catch (e) {
      setError((e as Error).message);
      throw e;
    } finally {
      setBusy("");
    }
  }
  const act = (label: string, task: () => Promise<unknown>) => {
    void perform(label, task).catch(() => {});
  };
  const canSwitch = () => {
    if (dirty) {
      setError(
        "Save the current draft or use Discard changes before opening another agreement.",
      );
      return false;
    }
    return true;
  };
  function newPlan() {
    if (!canSwitch()) return;
    setPlan(blankPlan());
    setSaved(null);
    setEdited(false);
    setCheck(null);
    setTab("document");
    setSelected("");
    setNotice(
      "New unsigned agreement. Start with a question, then choose evidence and executable steps.",
    );
  }
  function chooseAudit(audit: Audit) {
    if (!canSwitch()) return;
    act("Opening audit", async () => {
      const template =
        audit.plan ??
        data?.plans
          .map(asSaved)
          .find((p) => p.plan.id === (audit.plan_id ?? audit.id))?.plan;
      if (template) {
        load({
          plan: {
            ...template,
            id: `${template.id}-${Date.now().toString(36)}`,
          },
          digest: "",
        });
        return;
      }
      if (audit.plan_id) {
        const value = await api.loadPlan(audit.plan_id);
        load({
          plan: {
            ...value.plan,
            id: `${value.plan.id}-${Date.now().toString(36)}`,
          },
          digest: "",
        });
        return;
      }
      const next = blankPlan();
      next.title = audit.title;
      next.question = audit.question;
      load({ plan: next, digest: "" });
      setNotice(
        "This catalog entry has no executable plan package. Its question is copied into an unsigned draft; add criteria and method. Catalog text is not an executable audit.",
      );
    });
  }
  function save() {
    act("Saving agreement", async () => {
      const priorDigest = saved?.plan.id === plan.id ? saved.digest : undefined;
      const result = await api.savePlan(plan, priorDigest);
      const validPlan = parsePlan(pretty(result.plan));
      if (!result.digest)
        throw new Error(
          "Save returned no digest. Approval and execution remain unavailable.",
        );
      setSaved({ ...result, plan: validPlan });
      setPlan(validPlan);
      setEdited(false);
      setData((prev) =>
        prev
          ? {
              ...prev,
              plans: [
                ...prev.plans.filter(
                  (p) => asSaved(p).plan.id !== validPlan.id,
                ),
                result,
              ],
            }
          : prev,
      );
      setNotice(
        "Saved. Approval binds this exact digest; previous approvals do not transfer to edits.",
      );
    });
  }
  function start(mode: "rehearse" | "audit") {
    if (!saved || dirty || !currentCheck?.valid) return;
    act(
      mode === "rehearse" ? "Starting rehearsal" : "Requesting admission",
      async () => {
        let result: { run_id: string };
        try {
          result = await api.run(
            saved.plan.id,
            plan.scope.dataset_ids[0],
            mode,
          );
        } catch (e) {
          if (
            e instanceof ApiError &&
            (e.status === 403 ||
              /blocked|consent|approval|admission/.test(e.code))
          ) {
            setRunId("");
            setRun({
              status: "blocked",
              plan_digest: saved.digest,
              dataset_id: plan.scope.dataset_ids[0],
              mode,
              error: { code: e.code, message: e.message },
              message: "The API refused admission. No run ID was issued.",
            });
            setTab("results");
          }
          throw e;
        }
        if (!result.run_id) throw new Error("Run API returned no run_id.");
        setRun({
          run_id: result.run_id,
          status: "queued",
          plan_digest: saved.digest,
          dataset_id: plan.scope.dataset_ids[0],
          mode,
        });
        setRunId(result.run_id);
        setTab("results");
      },
    );
  }
  async function refreshApprovals(expected: SavedPlan) {
    const current = await api.loadPlan(expected.plan.id);
    if (
      current.digest !== expected.digest ||
      pretty(current.plan) !== pretty(expected.plan)
    )
      throw new Error(
        "The saved agreement changed on the server. Reload it before approving or running again.",
      );
    setSaved(current);
  }
  async function importDataset() {
    const parsed: unknown = JSON.parse(importText);
    if (
      !isObject(parsed) ||
      typeof parsed.id !== "string" ||
      !isObject(parsed.manifest)
    )
      throw new Error("Expected a dataset with ID and manifest.");
    await api.importDataset(parsed);
    const result = await api.datasets();
    setData((prev) => (prev ? { ...prev, datasets: result.datasets } : prev));
    setImportText("");
    setImportError("");
    setNotice(
      "Dataset imported. Review its manifest and coverage before selecting it.",
    );
  }
  const blockers = executionBlockers(plan, saved, check, !!dataset);
  const ready = online && !blockers.length && !active && !busy;
  const tabs: [Tab, string][] = [
    ["document", "Agreement"],
    ["graph", "Graph"],
    ["code", "Code"],
    ["changes", "Changes"],
    ["results", "Results"],
    ["activity", "Accountability"],
  ];
  return (
    <div className="wb">
      <header className="wb-header">
        <div className="wb-wordmark">
          <a href="?workbench=1">
            AIAB<span> / AUDIT WORKBENCH</span>
          </a>
          <small>Evidence before conclusions.</small>
        </div>
        <div className="wb-header-right">
          <span className={`wb-connection ${online ? "online" : ""}`}>
            {loading
              ? "Connecting…"
              : online
                ? "API connected"
                : "Backend unavailable"}
          </span>
          <a href="?">Legacy builder ↗</a>
        </div>
      </header>
      <div className="wb-layout">
        <aside className="wb-library">
          <div className="wb-library-head">
            <span className="wb-kicker">Investigations</span>
            <button disabled={!!busy} onClick={newPlan}>
              + New agreement
            </button>
          </div>
          <TextField
            label={`Audit catalog · ${data?.audits.length ?? 0} / 27`}
            value={search}
            onChange={setSearch}
            help="Search the API-provided catalog. No built-in catalog is substituted."
          />
          <div className="wb-catalog">
            {data?.audits
              .filter((a) =>
                `${a.title} ${a.question}`
                  .toLowerCase()
                  .includes(search.toLowerCase()),
              )
              .map((a, i) => (
                <details key={a.id}>
                  <summary>
                    <span className="wb-index">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    {a.title}
                  </summary>
                  <p>{a.question}</p>
                  {a.requirements !== undefined && (
                    <JsonDetails
                      title="Evidence requirements"
                      value={a.requirements}
                    />
                  )}
                  {a.method && <p>{a.method}</p>}
                  {a.validity && (
                    <p className="wb-muted">Validity: {a.validity}</p>
                  )}
                  <button disabled={!!busy} onClick={() => chooseAudit(a)}>
                    Start this investigation →
                  </button>
                </details>
              ))}
          </div>
          {!data?.audits.length && (
            <p className="wb-muted">
              {loading
                ? "Loading the catalog…"
                : "Catalog unavailable. You can author a draft locally; execution needs the backend."}
            </p>
          )}
          <button
            disabled={!!busy || loading}
            onClick={() =>
              act("Refreshing catalog", async () => {
                const result = await api.catalog();
                setData((p) => (p ? { ...p, audits: result.audits } : p));
              })
            }
          >
            Refresh catalog
          </button>
          <h3>Saved agreements</h3>
          {data?.plans.map(asSaved).map((p) => (
            <button
              className="wb-saved"
              key={p.plan.id}
              disabled={!!busy}
              onClick={() => {
                if (canSwitch())
                  act("Loading agreement", async () =>
                    load(await api.loadPlan(p.plan.id)),
                  );
              }}
            >
              {p.plan.title}
              <small>{p.plan.id}</small>
            </button>
          ))}
          <div className="wb-library-foot">
            <p>Personal · Joint · Confidential</p>
            <small>
              Authorization and source completeness are checked by the service.
              Synthetic evidence remains labeled.
            </small>
          </div>
        </aside>
        <main className="wb-main">
          <div className="wb-title">
            <div>
              <span className="wb-kicker">
                {saved ? "Saved agreement" : "Unsigned draft"} / {plan.mode}
              </span>
              <h1>{plan.title}</h1>
              <p>
                {dirty
                  ? "Unsaved changes · prior checks and approvals do not authorize this draft."
                  : saved
                    ? "Agreement saved · inspect the digest and recipient permissions before approval."
                    : "Start from a question, a worked audit, or a blank agreement."}
              </p>
            </div>
            <div className="wb-actions">
              <button
                disabled={!!busy}
                onClick={() => {
                  const blob = new Blob([snapshot], {
                    type: "application/json",
                  });
                  const url = URL.createObjectURL(blob);
                  const link = document.createElement("a");
                  link.href = url;
                  link.download = `${plan.id.replace(/[^a-z0-9_-]/gi, "_")}.json`;
                  link.click();
                  setTimeout(() => URL.revokeObjectURL(url), 0);
                }}
              >
                Export JSON
              </button>
              <button
                className="wb-primary"
                disabled={!online || !!busy}
                onClick={save}
              >
                {busy === "Saving agreement" ? "Saving…" : "Save agreement"}
              </button>
            </div>
          </div>
          <div className="wb-feedback" aria-live="polite">
            {error && (
              <div role="alert" className="wb-error">
                {error}
              </div>
            )}
            {notice && <div className="wb-notice">{notice}</div>}
            {!online && !loading && (
              <div className="wb-notice">
                Local draft editing is available. Backend, inference, approvals
                and execution are unavailable.{" "}
                <button onClick={() => void bootstrap()}>Reconnect</button>
              </div>
            )}
            {busy && <p role="status">{busy}…</p>}
          </div>
          <nav className="wb-tabs" aria-label="Agreement views">
            {tabs.map(([key, label]) => (
              <button
                key={key}
                aria-current={tab === key ? "page" : undefined}
                onClick={() => setTab(key)}
              >
                {label}
                {key === "changes" && dirty ? " •" : ""}
                {key === "results" && active ? " ◌" : ""}
              </button>
            ))}
          </nav>
          {(tab === "document" ||
            tab === "graph" ||
            tab === "code" ||
            tab === "changes") && (
            <fieldset className="wb-editable" disabled={!!busy}>
              {tab === "document" && (
                <Editor
                  plan={plan}
                  onChange={update}
                  selected={selected}
                  onSelect={setSelected}
                />
              )}
              {tab === "graph" && (
                <GraphView
                  plan={plan}
                  selected={selected}
                  onSelect={setSelected}
                  onChange={update}
                />
              )}
              {tab === "code" && (
                <CodeView plan={plan} selected={selected} onApply={update} />
              )}
              {tab === "changes" && (
                <Changes
                  baseline={saved?.plan ?? null}
                  plan={plan}
                  onChange={update}
                />
              )}
            </fieldset>
          )}
          {tab === "results" && (
            <>
              <form
                className="wb-open-run"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!openRunId.trim()) return;
                  setRun({ status: "loading", run_id: openRunId.trim() });
                  setRunId(openRunId.trim());
                  setPollEpoch((n) => n + 1);
                }}
              >
                <TextField
                  label="Open an existing authorized run"
                  value={openRunId}
                  onChange={setOpenRunId}
                  help="Enter a product run ID. The API checks account access; changing this view does not cancel another run."
                />
                <button
                  type="submit"
                  disabled={!online || !!busy || !openRunId.trim()}
                >
                  Load run
                </button>
                {run && (
                  <button
                    type="button"
                    disabled={!!busy}
                    onClick={() => {
                      setRunId("");
                      setRun(null);
                      setPollError("");
                      setNotice(
                        "Run view cleared. This stops monitoring; any active execution continues until cancelled or completed by the service.",
                      );
                    }}
                  >
                    Clear run view
                  </button>
                )}
              </form>
              {pollError && (
                <div role="alert" className="wb-error">
                  {pollError}
                  <button onClick={() => setPollEpoch((e) => e + 1)}>
                    Resume status polling
                  </button>
                </div>
              )}
              <Results
                key={runId}
                run={run}
                busy={!!busy}
                onCancel={() =>
                  act("Cancelling run", async () => {
                    await api.cancel(runId);
                    setPollEpoch((e) => e + 1);
                    setNotice(
                      "Cancellation requested. Waiting for the execution service to confirm its state.",
                    );
                  })
                }
                onRefresh={() => setPollEpoch((e) => e + 1)}
                onChallenge={(body) =>
                  perform("Submitting challenge", async () => {
                    await api.challenge(runId, body);
                    setPollEpoch((e) => e + 1);
                  })
                }
              />
            </>
          )}
          {tab === "activity" && (
            <section className="wb-activity">
              <h2>Accountability history</h2>
              <p>
                Only records authorized for the authenticated account are
                requested. Private self-audits and refusal identities must be
                protected by the backend policy.
              </p>
              <button
                disabled={!online || !!busy}
                onClick={() =>
                  act("Loading accountability records", async () => {
                    const h = await api.history();
                    setHistory(h.history ?? h.records ?? h);
                  })
                }
              >
                Load authorized history
              </button>
              <JsonDetails
                title="Request, approval, execution, disclosure & challenge records"
                value={history}
                open
              />
              <JsonDetails
                title="Authenticated account & deployed capabilities"
                value={{ user: data?.user, capabilities: data?.capabilities }}
              />
            </section>
          )}
        </main>
        <aside className="wb-rail">
          <section>
            <span className="wb-kicker">Evidence readiness</span>
            <h3>Choose the observed universe</h3>
            <Field label="Run dataset">
              <select
                disabled={!!busy || !online}
                value={plan.scope.dataset_ids[0] ?? ""}
                onChange={(e) =>
                  update({
                    ...plan,
                    scope: {
                      ...plan.scope,
                      dataset_ids: e.target.value ? [e.target.value] : [],
                    },
                  })
                }
              >
                <option value="">Select a dataset…</option>
                {data?.datasets.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.title} · {d.origin}
                  </option>
                ))}
              </select>
            </Field>
            {dataset ? (
              <DatasetSummary
                dataset={dataset}
                required={plan.scope.required_sources}
              />
            ) : (
              <p>
                No manifest selected. Coverage and source authenticity are
                unknown.
              </p>
            )}
            {plan.scope.dataset_ids.length > 1 && (
              <p className="wb-error">
                This run endpoint accepts one dataset. Select a single admitted
                manifest.
              </p>
            )}
            <details>
              <summary>Import evidence</summary>
              <p>
                JSON is sent to the authenticated UI backend. This importer is
                for synthetic or otherwise authorized non-confidential data. A
                verified sealed-submission client is not configured here.
              </p>
              <fieldset
                disabled={!!busy || !online || plan.mode === "confidential"}
              >
                <input
                  type="file"
                  ref={fileRef}
                  accept="application/json,.json"
                  aria-label="Import dataset JSON file"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file)
                      void file
                        .text()
                        .then((text) => {
                          setImportText(text);
                          setImportError("");
                        })
                        .catch((e) => setImportError((e as Error).message));
                  }}
                />
                <TextField
                  label="Dataset JSON"
                  multiline
                  value={importText}
                  onChange={setImportText}
                />
                <button
                  disabled={!importText.trim()}
                  onClick={() => {
                    setImportError("");
                    act("Importing dataset", importDataset);
                  }}
                >
                  Validate & import dataset
                </button>
              </fieldset>
              {plan.mode === "confidential" && (
                <p className="wb-notice">
                  Raw import is unavailable in confidential mode. Use an
                  approved sealed collector.
                </p>
              )}
              {importError && <p className="wb-error">{importError}</p>}
            </details>
          </section>
          <section className="wb-execute">
            <span className="wb-kicker">Validate → authorize → execute</span>
            <h3>Test this agreement</h3>
            <JsonDetails
              title="Deployed runtime & inference capabilities"
              value={
                data?.capabilities && Object.keys(data.capabilities).length
                  ? data.capabilities
                  : "Inference availability is not reported. No live model has been verified by this page; Check plan and admission must verify the required runtime."
              }
            />
            <p className="wb-muted">
              All execution uses actual methods. Inference requires verified
              DeepSeek/Tinfoil; no fallback results are generated.
            </p>
            <button
              className="wb-wide"
              disabled={!online || !!busy}
              onClick={() =>
                act("Checking plan", async () => {
                  const result = await api.check(plan);
                  setCheck({ snapshot, result });
                })
              }
            >
              01 / Check plan
            </button>
            {currentCheck && (
              <div className={currentCheck.valid ? "wb-notice" : "wb-error"}>
                <strong>
                  {currentCheck.valid
                    ? "Backend check passed"
                    : "Plan is not ready"}
                </strong>
                {currentCheck.diagnostics?.map((d, i) => (
                  <p key={i}>
                    {typeof d === "string"
                      ? d
                      : `${d.severity ?? "diagnostic"}: ${d.message}${d.path ? ` (${d.path})` : ""}`}
                  </p>
                ))}
                <JsonDetails
                  title="Runtime & evidence requirements"
                  value={currentCheck.requirements}
                />
              </div>
            )}
            {check && !currentCheck && (
              <p className="wb-notice">The draft changed. Check it again.</p>
            )}
            <details>
              <summary>Local authoring checks · {issues.length}</summary>
              <p>Advisory checks, not runtime or authorization verification.</p>
              {issues.map((s, i) => (
                <p key={i}>{s}</p>
              ))}
            </details>
            <button
              className="wb-wide"
              disabled={!ready}
              onClick={() => start("rehearse")}
            >
              02 / Rehearse actual methods
            </button>
            <small>
              {dataset?.origin === "synthetic"
                ? "Synthetic fixture selected. Execution is real; evidence is fictional."
                : "Rehearsal uses the selected authorized dataset; privacy rules still apply."}
            </small>
            <button
              className="wb-wide wb-primary"
              disabled={!ready}
              onClick={() => start("audit")}
            >
              03 / Request audit run
            </button>
            {!ready && (
              <p className="wb-muted">
                {blockers.join(" ")}{" "}
                {active ? "An active run must finish first." : ""} Admission
                checks approvals and permissions.
              </p>
            )}
            <button
              className="wb-wide"
              disabled
              title="No evaluation endpoint is defined in the v1 HTTP contract."
            >
              Evaluate with DeepSeek · unavailable
            </button>
            <small>
              Labeled-case evaluation requires a backend evaluation API and
              verified inference. Rehearsal is not an evaluation score.
            </small>
            {active && (
              <button className="wb-wide" onClick={() => setTab("results")}>
                Inspect active run →
              </button>
            )}
          </section>
          <section>
            <span className="wb-kicker">Exact agreement</span>
            <h3>Review & consent</h3>
            <p className="wb-digest">
              {saved && !dirty
                ? saved.digest
                : "Unsigned draft · no current saved digest"}
            </p>
            <p>
              Approving signs the displayed saved agreement. It does not approve
              future edits.
            </p>
            <div className="wb-actions">
              <button
                disabled={!online || !!busy || !saved || dirty}
                onClick={() =>
                  saved &&
                  act("Approving agreement", async () => {
                    await api.approve(saved.plan.id, saved.digest);
                    await refreshApprovals(saved);
                    setNotice(
                      "Authorization submitted for the displayed digest. Admission checks all required approvals.",
                    );
                  })
                }
              >
                Approve digest
              </button>
              <button
                disabled={!online || !!busy || !saved}
                onClick={() =>
                  saved &&
                  act("Revoking authorization", async () => {
                    await api.revoke(saved.plan.id, saved.digest);
                    await refreshApprovals(saved);
                    setNotice(
                      "Authorization revoked for the saved digest. Already released information cannot be recalled.",
                    );
                  })
                }
              >
                Revoke
              </button>
            </div>
            {dirty && (
              <button
                disabled={!!busy}
                onClick={() => {
                  setPlan(saved?.plan ?? blankPlan());
                  setEdited(false);
                  setCheck(null);
                  setNotice("Unsaved changes discarded.");
                }}
              >
                Discard changes
              </button>
            )}
            <JsonDetails
              title="Authorized approval state"
              value={
                dirty
                  ? "Draft changed; previous approvals do not authorize these edits."
                  : saved?.approvals
              }
            />
            <RecipientPreview plan={plan} />
          </section>
          <section>
            <h3>Review notes</h3>
            <p className="wb-muted">
              Local to this page; not signed, shared or persisted. Backend
              review-thread storage is not defined.
            </p>
            <Field label="Clause">
              <select
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
              >
                <option value="">Whole agreement</option>
                {plan.steps.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.title}
                  </option>
                ))}
              </select>
            </Field>
            <TextField
              label="Named review responsibility"
              value={responsibility}
              onChange={setResponsibility}
            />
            <TextField
              label="Question or comment"
              multiline
              value={noteText}
              onChange={setNoteText}
            />
            <button
              disabled={!noteText.trim()}
              onClick={() => {
                setNotes((prev) => [
                  ...prev,
                  {
                    id: Date.now(),
                    clause: selected || "agreement",
                    author: "Current reviewer",
                    text: noteText,
                    responsibility,
                    resolved: false,
                  },
                ]);
                setNoteText("");
              }}
            >
              Add local note
            </button>
            {notes.map((n) => (
              <div className="wb-note" key={n.id}>
                <small>
                  {n.clause} · {n.responsibility || "Unassigned"} ·{" "}
                  {n.resolved ? "Resolved" : "Open"}
                </small>
                <p>{n.text}</p>
                <button
                  onClick={() =>
                    setNotes((prev) =>
                      prev.map((x) =>
                        x.id === n.id ? { ...x, resolved: !x.resolved } : x,
                      ),
                    )
                  }
                >
                  {n.resolved ? "Reopen" : "Resolve"}
                </button>
              </div>
            ))}
          </section>
        </aside>
      </div>
    </div>
  );
}

function DatasetSummary({
  dataset,
  required,
}: {
  dataset: Dataset;
  required: string[];
}) {
  const sources = dataset.manifest.sources ?? [];
  const available = sources.map((s) =>
    typeof s === "string"
      ? s
      : isObject(s)
        ? String(s.id ?? s.source ?? s.name ?? s.type ?? "unknown")
        : "unknown",
  );
  return (
    <>
      <span
        className={`wb-origin ${dataset.origin === "synthetic" ? "synthetic" : ""}`}
      >
        {dataset.origin} evidence
      </span>
      <p>
        {dataset.origin === "synthetic"
          ? "Fictional evidence for development or demonstration. Native source completeness is not established."
          : "Import origin alone does not establish native authenticity or completeness."}
      </p>
      <div className="wb-source-list">
        {required.map((s) => (
          <p key={s}>
            <span>
              {available.includes(s)
                ? "Listed in manifest"
                : "Missing / unverified"}
            </span>
            {s}
          </p>
        ))}
      </div>
      <JsonDetails
        title="Manifest, coverage, integrity & collection gaps"
        value={dataset.manifest}
        open
      />
      <small>Available sources: {available.join(", ") || "Not declared"}</small>
    </>
  );
}
