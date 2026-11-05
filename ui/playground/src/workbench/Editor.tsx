import { useEffect, useState, type ReactNode } from "react";
import { csv, describeStep, isObject, parsePlan, pretty } from "./model";
import { STEP_KINDS, type Plan, type Step, type StepKind } from "./types";

export function Field({
  label,
  children,
  help,
}: {
  label: string;
  children: ReactNode;
  help?: string;
}) {
  return (
    <label className="wb-field">
      <span>{label}</span>
      {children}
      {help && <small>{help}</small>}
    </label>
  );
}
export function TextField({
  label,
  value,
  onChange,
  help,
  multiline = false,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  help?: string;
  multiline?: boolean;
  type?: string;
}) {
  // Scope/recipient lists normalize into arrays immediately. Preserve the raw
  // focused buffer so a trailing comma can be typed before the next identity.
  const [focused, setFocused] = useState(false);
  const [buffer, setBuffer] = useState(value);
  const inputProps = {
    value: focused ? buffer : value,
    onFocus: () => {
      setBuffer(value);
      setFocused(true);
    },
    onBlur: () => setFocused(false),
    onChange: (
      event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
    ) => {
      setBuffer(event.target.value);
      onChange(event.target.value);
    },
  };
  return (
    <Field label={label} help={help}>
      {multiline ? (
        <textarea rows={3} {...inputProps} />
      ) : (
        <input type={type} {...inputProps} />
      )}
    </Field>
  );
}
export function JsonEditor({
  label,
  value,
  onApply,
}: {
  label: string;
  value: unknown;
  onApply: (value: Record<string, unknown>) => void;
}) {
  const [text, setText] = useState(pretty(value));
  const [error, setError] = useState("");
  useEffect(() => {
    setText(pretty(value));
    setError("");
  }, [value]);
  return (
    <div className="wb-json-editor">
      <Field label={label}>
        <textarea
          spellCheck={false}
          className="mono-area"
          rows={8}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      </Field>
      <button
        type="button"
        disabled={text === pretty(value)}
        onClick={() => {
          try {
            const v: unknown = JSON.parse(text);
            if (!isObject(v)) throw new Error("Expected a JSON object.");
            onApply(v);
            setError("");
          } catch (e) {
            setError((e as Error).message);
          }
        }}
      >
        Apply settings
      </button>
      {text !== pretty(value) && (
        <small>
          Unapplied settings are excluded from saving and execution.
        </small>
      )}
      {error && (
        <p role="alert" className="wb-error">
          {error}
        </p>
      )}
    </div>
  );
}
const verbs: Record<StepKind, string> = {
  select: "Find evidence",
  join: "Link records",
  aggregate: "Measure",
  rules: "Compare against rules",
  sql: "Query with SQL",
  python: "Compute with Python",
  agent: "Investigate",
  assert: "Check evidence",
  release: "Release findings",
};
function Section({
  id,
  number,
  title,
  children,
}: {
  id: string;
  number: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section id={`clause-${id}`} className="wb-clause">
      <header>
        <span className="wb-kicker">{number}</span>
        <h2>{title}</h2>
      </header>
      {children}
    </section>
  );
}
export default function Editor({
  plan,
  onChange,
  selected,
  onSelect,
}: {
  plan: Plan;
  onChange: (p: Plan) => void;
  selected: string;
  onSelect: (id: string) => void;
}) {
  const patch = (p: Partial<Plan>) => onChange({ ...plan, ...p });
  const scope = (p: Partial<Plan["scope"]>) =>
    patch({ scope: { ...plan.scope, ...p } });
  const stepPatch = (index: number, p: Partial<Step>) =>
    patch({
      steps: plan.steps.map((s, i) => (i === index ? { ...s, ...p } : s)),
    });
  const [kind, setKind] = useState<StepKind>("agent");
  return (
    <div className="wb-document">
      <Section id="question" number="01" title="Question & criteria">
        <TextField
          label="Agreement title"
          value={plan.title}
          onChange={(title) => patch({ title })}
        />
        <TextField
          label="What decision should this investigation support?"
          value={plan.question}
          multiline
          onChange={(question) => patch({ question })}
        />
        {plan.criteria.map((c, i) => (
          <div className="wb-card" key={i}>
            <TextField
              label="Criterion ID"
              value={c.id}
              onChange={(id) =>
                patch({
                  criteria: plan.criteria.map((x, j) =>
                    j === i ? { ...x, id } : x,
                  ),
                })
              }
            />
            <TextField
              label="Decision rule, alternatives & inconclusive conditions"
              value={c.description}
              multiline
              onChange={(description) =>
                patch({
                  criteria: plan.criteria.map((x, j) =>
                    j === i ? { ...x, description } : x,
                  ),
                })
              }
            />
            <button
              type="button"
              onClick={() =>
                patch({ criteria: plan.criteria.filter((_, j) => i !== j) })
              }
            >
              Remove criterion
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() =>
            patch({
              criteria: [
                ...plan.criteria,
                { id: `criterion-${Date.now().toString(36)}`, description: "" },
              ],
            })
          }
        >
          + Add decision rule
        </button>
      </Section>
      <Section id="evidence" number="02" title="Evidence & boundaries">
        <p>
          Only evidence authorized at admission can be inspected. Empty
          collections leave scope unspecified; they never grant access.
        </p>
        <div className="wb-grid2">
          <TextField
            label="Subjects / identity-map references"
            value={plan.scope.subjects.join(", ")}
            onChange={(v) => scope({ subjects: csv(v) })}
            help="Comma-separated authoritative identities; personal mode still requires the authenticated subject."
          />
          <TextField
            label="Required sources"
            value={plan.scope.required_sources.join(", ")}
            onChange={(v) => scope({ required_sources: csv(v) })}
          />
          <TextField
            label="Period starts (ISO timestamp)"
            value={plan.scope.time_start ?? ""}
            onChange={(v) => scope({ time_start: v || null })}
          />
          <TextField
            label="Period ends (ISO timestamp)"
            value={plan.scope.time_end ?? ""}
            onChange={(v) => scope({ time_end: v || null })}
          />
        </div>
        <TextField
          label="Allowed fields"
          value={plan.scope.fields.join(", ")}
          onChange={(v) => scope({ fields: csv(v) })}
        />
        <details>
          <summary>
            Versions, identity mappings, exclusions & coverage settings
          </summary>
          <JsonEditor
            label="Complete scope contract"
            value={plan.scope}
            onApply={(v) => {
              const next = { ...plan.scope, ...v };
              if (
                [
                  next.dataset_ids,
                  next.subjects,
                  next.fields,
                  next.required_sources,
                ].some(
                  (x) =>
                    !Array.isArray(x) || x.some((y) => typeof y !== "string"),
                )
              )
                throw new Error("Scope collections must be string arrays.");
              scope(next);
            }}
          />
        </details>
      </Section>
      <Section id="investigation" number="03" title="Investigation method">
        <p>
          Compose an ordered investigation. Inputs link to earlier steps; agent
          settings expose adaptive behavior, tools and stopping conditions.
        </p>
        {plan.steps.map((s, i) => (
          <article
            key={i}
            className={`wb-step ${selected === s.id ? "selected" : ""}`}
          >
            <button
              type="button"
              className="wb-step-anchor"
              onClick={() => onSelect(s.id)}
              aria-pressed={selected === s.id}
            >
              {String(i + 1).padStart(2, "0")} / {s.kind} · Highlight linked
              graph/code
            </button>
            <TextField
              label="Step title"
              value={s.title}
              onChange={(title) => stepPatch(i, { title })}
            />
            <p>{describeStep(s)}</p>
            <div className="wb-grid2">
              <TextField
                label="Stable step ID"
                value={s.id}
                onChange={(id) => stepPatch(i, { id })}
              />
              <Field label="Operation">
                <select
                  value={s.kind}
                  onChange={(e) =>
                    stepPatch(i, { kind: e.target.value as StepKind })
                  }
                >
                  {STEP_KINDS.map((k) => (
                    <option key={k} value={k}>
                      {verbs[k]}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <TextField
              label="Inputs · preceding step IDs"
              value={s.inputs.join(", ")}
              onChange={(v) => stepPatch(i, { inputs: csv(v) })}
            />
            <details>
              <summary>
                Executable settings ·{" "}
                {s.kind === "sql" || s.kind === "python"
                  ? "custom code"
                  : "operator configuration"}
              </summary>
              <JsonEditor
                label={
                  s.kind === "sql" || s.kind === "python"
                    ? "Custom code & explicit input/output contract (JSON)"
                    : "Executable operator settings (JSON)"
                }
                value={s.config}
                onApply={(config) => stepPatch(i, { config })}
              />
            </details>
            <div className="wb-actions">
              <button
                type="button"
                disabled={i === 0}
                onClick={() => {
                  const steps = [...plan.steps];
                  [steps[i - 1], steps[i]] = [steps[i], steps[i - 1]];
                  patch({ steps });
                }}
              >
                Move earlier
              </button>
              <button
                type="button"
                disabled={i === plan.steps.length - 1}
                onClick={() => {
                  const steps = [...plan.steps];
                  [steps[i + 1], steps[i]] = [steps[i], steps[i + 1]];
                  patch({ steps });
                }}
              >
                Move later
              </button>
              <button
                type="button"
                onClick={() =>
                  patch({ steps: plan.steps.filter((_, j) => i !== j) })
                }
              >
                Remove step
              </button>
            </div>
          </article>
        ))}
        <div className="wb-actions">
          <select
            aria-label="New operation"
            value={kind}
            onChange={(e) => setKind(e.target.value as StepKind)}
          >
            {STEP_KINDS.map((k) => (
              <option key={k} value={k}>
                {verbs[k]}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => {
              const id = `step-${Date.now().toString(36)}`;
              patch({
                steps: [
                  ...plan.steps,
                  {
                    id,
                    kind,
                    title: verbs[kind],
                    inputs: plan.steps.length
                      ? [plan.steps[plan.steps.length - 1].id]
                      : [],
                    config: {},
                  },
                ],
              });
              onSelect(id);
            }}
          >
            + Add step
          </button>
        </div>
      </Section>
      <Section id="checks" number="04" title="Checks & counterevidence">
        <p>
          Define counterevidence searches, reference validation and missing-data
          behavior as executable assertion or investigation settings. An empty
          search cannot establish absence without sufficient coverage.
        </p>
        {plan.steps
          .filter((s) => s.kind === "assert")
          .map((s) => (
            <button
              type="button"
              className="wb-check-link"
              key={s.id}
              onClick={() => {
                onSelect(s.id);
                document
                  .getElementById("clause-investigation")
                  ?.scrollIntoView({ behavior: "smooth" });
              }}
            >
              {s.title} → {s.id}
            </button>
          ))}
        <button
          type="button"
          onClick={() => {
            const id = `check-${Date.now().toString(36)}`;
            patch({
              steps: [
                ...plan.steps,
                {
                  id,
                  kind: "assert",
                  title: "Validate evidence and coverage",
                  inputs: plan.steps.length
                    ? [plan.steps[plan.steps.length - 1].id]
                    : [],
                  config: {},
                },
              ],
            });
            onSelect(id);
          }}
        >
          + Add executable check
        </button>
        <p className="wb-muted">
          Checks with no configured operator rules remain incomplete. Use Check
          plan to ask the backend to validate them.
        </p>
      </Section>
      <Section id="disclosure" number="05" title="Disclosure agreement">
        <p>
          Recipient projections are enforced by the release service. Small-group
          suppression does not establish differential privacy.
        </p>
        {plan.disclosure.recipients.map((r, i) => {
          const change = (v: Partial<typeof r>) =>
            patch({
              disclosure: {
                ...plan.disclosure,
                recipients: plan.disclosure.recipients.map((x, j) =>
                  i === j ? { ...x, ...v } : x,
                ),
              },
            });
          return (
            <div className="wb-card" key={i}>
              <div className="wb-grid2">
                <TextField
                  label="Recipient ID"
                  value={r.id}
                  onChange={(id) => change({ id })}
                />
                <TextField
                  label="Recipient role"
                  value={r.role}
                  onChange={(role) => change({ role })}
                />
              </div>
              <TextField
                label="Released fields"
                value={r.fields.join(", ")}
                onChange={(v) => change({ fields: csv(v) })}
              />
              <label className="wb-toggle">
                <input
                  type="checkbox"
                  checked={r.allow_evidence}
                  onChange={(e) => change({ allow_evidence: e.target.checked })}
                />{" "}
                Permit authorized evidence references
              </label>
              <label className="wb-toggle">
                <input
                  type="checkbox"
                  checked={r.allow_excerpts}
                  onChange={(e) => change({ allow_excerpts: e.target.checked })}
                />{" "}
                Permit approved excerpts
              </label>
              <button
                type="button"
                onClick={() =>
                  patch({
                    disclosure: {
                      ...plan.disclosure,
                      recipients: plan.disclosure.recipients.filter(
                        (_, j) => i !== j,
                      ),
                    },
                  })
                }
              >
                Remove recipient
              </button>
            </div>
          );
        })}
        <button
          type="button"
          onClick={() =>
            patch({
              disclosure: {
                ...plan.disclosure,
                recipients: [
                  ...plan.disclosure.recipients,
                  {
                    id: "",
                    role: "",
                    fields: ["findings", "coverage"],
                    allow_evidence: false,
                    allow_excerpts: false,
                  },
                ],
              },
            })
          }
        >
          + Add recipient
        </button>
        <div className="wb-grid2">
          {(["min_group_size", "max_releases"] as const).map((k) => (
            <TextField
              key={k}
              label={
                k === "min_group_size"
                  ? "Minimum disclosed group size"
                  : "Maximum releases"
              }
              type="number"
              value={String(plan.disclosure[k])}
              onChange={(v) =>
                patch({ disclosure: { ...plan.disclosure, [k]: Number(v) } })
              }
            />
          ))}
        </div>
        <details>
          <summary>
            Cumulative release policy & additional disclosure settings
          </summary>
          <JsonEditor
            label="Disclosure contract"
            value={plan.disclosure}
            onApply={(v) =>
              onChange(
                parsePlan(
                  pretty({ ...plan, disclosure: { ...plan.disclosure, ...v } }),
                ),
              )
            }
          />
        </details>
      </Section>
      <Section id="operation" number="06" title="Approval & operation">
        <Field label="Investigation mode">
          <select
            value={plan.mode}
            onChange={(e) => patch({ mode: e.target.value as Plan["mode"] })}
          >
            <option value="personal">
              Personal · authenticated self scope
            </option>
            <option value="joint">
              Joint · explicitly authorized evidence
            </option>
            <option value="confidential">
              Confidential · restricted recipient reports
            </option>
          </select>
        </Field>
        <p>
          {plan.mode === "confidential"
            ? "Private inputs and traces stay inside the confidential boundary. The authoritative participant roster and all required approvals are checked by admission."
            : "The authenticated account and backend policy determine evidence access. A subject filter is not authorization."}
        </p>
        <TextField
          label="Required signer identities"
          value={plan.approval.required_signers.join(", ")}
          onChange={(v) =>
            patch({ approval: { ...plan.approval, required_signers: csv(v) } })
          }
        />
        <TextField
          label="Frozen participant roster"
          value={plan.approval.roster.join(", ")}
          onChange={(v) =>
            patch({ approval: { ...plan.approval, roster: csv(v) } })
          }
          help="Do not shrink the roster to bypass a refusal. Authoritative membership is checked by the backend."
        />
        <TextField
          label="Approval expiry (ISO timestamp)"
          value={plan.approval.expires_at ?? ""}
          onChange={(v) =>
            patch({ approval: { ...plan.approval, expires_at: v || null } })
          }
        />
        <p className="wb-model">
          DeepSeek <code>{plan.agent.model}</code> /{" "}
          <code>{plan.agent.provider}</code>
          <br />
          <small>
            Serving identity and attestation must be verified at execution.
          </small>
        </p>
        <div className="wb-grid2">
          {(
            [
              "max_turns",
              "max_tokens",
              "max_tool_calls",
              "max_seconds",
            ] as const
          ).map((k) => (
            <TextField
              key={k}
              label={k.replaceAll("_", " ")}
              type="number"
              value={String(plan.agent[k])}
              onChange={(v) =>
                patch({ agent: { ...plan.agent, [k]: Number(v) } })
              }
            />
          ))}
        </div>
        <details>
          <summary>Retention, cancellation, challenge & roster policy</summary>
          <p>
            Additional policy fields require backend support; Check plan reports
            compatibility. Unconfigured policies are unspecified.
          </p>
          <JsonEditor
            label="Approval and operation policy"
            value={plan.approval}
            onApply={(v) =>
              onChange(
                parsePlan(
                  pretty({ ...plan, approval: { ...plan.approval, ...v } }),
                ),
              )
            }
          />
        </details>
      </Section>
    </div>
  );
}
