# Workbench authoring and evaluation API

Parent integration: mount `server.workbench_assist.create_router(workbench)` on the
FastAPI application. It has its own `/api/workbench` prefix and uses the existing
`WorkbenchAPI.actor`, body parser, and JSON error route. Mount once, in one service
worker per evaluation database. The router lifespan cancels and drains evaluation
tasks at shutdown. `router.evaluation_jobs` also exposes the task registry to the
parent. No changes to the existing API, store, app, or frontend are required here.

All endpoints require an authenticated session. POST requires the exact configured
same origin. Actor, authorization, model, transport, evidence, truth, tool overrides,
and test-model inputs are rejected. Errors use
`{"error":{"code":"invalid_request","message":"Request invalid or unavailable."}}`
(400); existing authentication/origin/not-found errors retain 401/403/404.

## Unsigned suggestion

`POST /api/workbench/suggestions`

Input: `{"plan":{...},"instruction":"..."}`. Both fields are required; no others.
The plan must pass the shared structural and executable-operator validators and
be at most 64 KiB in canonical JSON. Instruction length is 1–4000 characters.
Evidence and credential containers are forbidden in plan extensions.

Output: `{"plan":{...},"changes":[{"path":"/scope/subjects","before":[...],"after":[...]}],"usage":{"prompt_tokens":123,"completion_tokens":45,"total_tokens":168,"test_model":false},"verification":{...}}`.
`verification` is the model adapter's verified Tinfoil document. `changes` contains
exact JSON-pointer differences, including complete before/after arrays. Plan ID,
version, and explicit root/scope identity maps must be preserved. Scope changes
remain proposed changes requiring review. The endpoint does not save, sign,
approve, or execute anything.

Only the draft plan and instruction enter the user message; the system message
contains the server-owned schema. No dataset, fixture labels, signing keys, or
secrets are retrieved. Tool calls, malformed/duplicate-key JSON, invalid operator
configs, extra response-envelope fields, changed identity mappings, and excess
usage are rejected. The authoring-only request omits tool declarations and uses
strict structured JSON output. The generation grammar declares existing draft
extension keys and omits unsupported `uniqueItems`; the unchanged shared validators
still enforce uniqueness and all executable constraints on the completed plan.

At most two verified completions are allowed, with aggregate reservations of
24,000 input tokens and 8,192 output tokens. The first completion reserves at most
4,096 output tokens; a correction may use only the remaining aggregate output
budget. Each attempt's complete counted input is reserved before inference.
Returned usage sums both attempts. Only malformed envelopes/JSON or invalid
structural/executable proposals qualify for one correction, using the original
draft and a fixed server-owned diagnostic. Rejected provider text and validation
exception strings are never echoed into correction input or diagnostics. Tool calls,
private payloads, identity changes and usage violations fail closed without retry.
The total 180-second deadline includes startup, token counting and correction;
close has a separate 10-second bound. There is no provider or transport fallback.

Correctable draft failures return safe 400 `invalid_plan`, `private_payload`, or
`invalid_instruction` errors. Aggregate input exhaustion returns 422
`suggestion_budget`; a second invalid proposal returns 422 `invalid_proposal`.
Verification/provider/protocol failures return safe 503 `suggestion_unavailable`,
including adapter `ValueError` subclasses; raw provider error bodies never escape.
`AIAB_DEEPSEEK_TRANSPORT=go` selects the already-qualified
official pinned broker through the real `DeepSeekModel`; credentials and reviewed
host remain server configuration. A Python-only `suggest(..., test_model=...)`
seam accepts explicitly labeled test models; HTTP cannot activate it.

## Durable synthetic evaluation

`POST /api/workbench/evaluations`

Input: `{"plan_id":"information-exposure","include_agent":false,"case_limit":16,"seed":0}`.
Only `plan_id` is required. `include_agent` is a strict boolean, default false;
`seed` is a strict integer 0–2147483647, default 0. Deterministic case limit defaults
to 16 and is bounded 1–16; agent case limit defaults to 4 and is bounded 1–4.
The seed controls fixture generation and cohort ordering; a changed fixture identity
universe may not match an explicitly scoped plan and is never silently remapped.
Unknown keys, including caller-supplied evidence modes or trust flags, fail.
Output: `{"evaluation_id":"<opaque id>"}` (200). At most two jobs can be active
across this database; capacity exhaustion yields 429 `evaluation_capacity`.

The saved plan must belong to the caller, explicitly scope only the `northstar`
dataset, and use catalog criterion IDs. Server-generated fixtures are the sole
evidence source. This does not authorize access to stored/imported evidence.
Plan subjects, fields, time, sources, mode, operators, and executable settings are
retained. In particular, a restrictive plan is not broadened to obtain a better
score. Generated subject capabilities cover only the server-created universe.

With `include_agent:false`, the exact evaluated plan removes agent steps and their
transitive dependants. The response records the resulting plan, digest, and exact
diff. An empty or incompatible derived graph fails admission. Its metrics describe
this derived deterministic plan and make no model-accuracy claim.

With `include_agent:true`, the plan must contain an agent step and the exact
reviewed model profile. Each complete case executes the actual plan through
`runner.execute_plan`, including the real DeepSeek adapter, Pi harness, and runtime
tools. Runtime/tool/profile failures produce explicit per-case unknown results;
there is no baseline substitution or mock inference. Service ceilings are 180
runtime seconds, 32,000 tokens, 12 turns, and 40 tool calls per case; plans exceeding
these fail admission rather than being silently rewritten. An external 200-second
case deadline and `case_limit * 200 + 30` second job deadline also apply.

Observed cases are grouped by criterion and complete investigation ID and selected
by seeded hash ordering of observed IDs. Selection never reads truth labels.
Only those complete cases are executed; the report gives available, selected, and
excluded counts. The default deterministic limit covers all 16 development cases
for a single catalog method. A bounded agent sample does not establish real-world
representativeness. Hidden world/answer records are generated separately in server
memory and used only for scoring after execution. They are never serialized into
model input, bootstrap, observed datasets, or evaluation responses.

`GET /api/workbench/evaluations/{id}` returns only to the owner:

```json
{
  "evaluation_id": "...",
  "status": "completed",
  "created": 0.0,
  "updated": 0.0,
  "plan_digest": "...",
  "evaluated_plan": {},
  "evaluated_digest": "...",
  "changes": [],
  "include_agent": false,
  "case_limit": 16,
  "seed": 0,
  "evidence_mode": "synthetic_full_trace",
  "metrics": {},
  "report": {}
}
```

Statuses: `queued`, `running`, `completed`, `failed`, `cancelled`, `interrupted`.
Metrics/report start null. Running report checkpoints contain `cases` and
`partial:true`. Completed report contains `execution`, `available_cases`,
`selected_cases`, `excluded_by_limit`, `selection`, `cases`,
`model_accuracy_claim:false`, and `limitations`. Each case has investigation and
criterion IDs, observed-event count, status (`evaluated` or `unknown`), outcome
(or null), and either safe failure code or runtime usage/coverage. No raw private
trace, model reasoning, expected labels, or per-case truth is returned.

Metrics fields: `cases`, `evaluated`, `unknown`, `abstentions`, `exact_matches`,
`accuracy`, `accuracy_denominator`, `tp`, `fp`,
`fn_including_unknown_and_abstention`, `tn`, `precision`,
`precision_denominator`, `recall`, `recall_denominator`, `citation_errors`.
Accuracy includes every selected case, including unknowns. Precision divides true
supported positives by predicted supported positives; recall divides by hidden
supportable positives. Undefined ratios are null. An abstention matches only a
hidden insufficient-evidence outcome; it never becomes a clean negative. Missing
or multiple candidate findings are unknown. Citation errors count references
outside that case's actual observed evidence. These are synthetic supportable
outcome metrics, not proof of semantic correctness or incremental model benefit.

Jobs and case checkpoints are durable in `workbench-evaluations.db` (SQLite WAL,
FULL synchronization, owner-filtered reads). A restart marks unfinished jobs
`interrupted`; it never resumes inference. Safe job-level failures add
`"error":"evaluation_failed"`. Completed results stay bound to their original
saved plan snapshot even if the draft changes later.

`POST /api/workbench/evaluations/{id}/cancel` accepts an empty JSON object or empty
body and returns `{"evaluation_id":"...","status":"cancelled"}`. Terminal jobs
retain their terminal status. Cancellation commits before task cancellation;
late completion cannot overwrite it. Owner-only GET/cancel return 404 to every
other principal, including plan participants. Cancellation preserves already
committed partial case checkpoints.

Local verified authoring regression (2026-11-05): the saved synthetic deterministic
agent-reconciliation plan passed admission. The previous shared request received
verified HTTP 400 for an empty tools declaration. A direct full-schema grammar
also received HTTP 400 for unsupported `uniqueItems`. The authoring-specific wire
request with compatible grammar returned HTTP 200 with `securityVerified: true`,
and the instruction “Change only the title to Review agent contributions. Preserve
all other fields exactly. Return the entire plan.” produced exactly `/title` as its
diff (2,296 prompt + 1,538 completion = 3,834 tokens). The existing scope extension
was retained. This used the reviewed host, real `DeepSeekModel` Go transport and
parent-qualified broker SHA-256
`437a648d172eb60cc761a1a611a0eb97bdc5b95e08fb99f16e1a3b2d3ae0043e`.
No runtime transport/model source, frontend, credentials, or private records were
changed or recorded. This local evidence does not assert a hosted deployment.

An end-to-end local authenticated `POST /api/workbench/suggestions` also returned
HTTP 200 through the real verified broker. Exact object equality confirmed the
result was the original plan with only `title` replaced; `test_model` was false,
`securityVerified` was true, and usage was 2,296 prompt + 1,732 completion = 4,028
tokens. No draft was saved or accepted by this request.
