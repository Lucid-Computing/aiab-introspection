# Workbench implementation contract

Implementation coordination for `AUDIT_WORKBENCH_PLAN.md`. New functionality uses
`aiab.workbench` and `/api/workbench`; existing signed graph plans keep their exact
semantics. This contract defines the first integration seam, not a waiver of the
full product plan. Add fields deliberately; never silently substitute providers or
claim synthetic evidence is native source verification.

## Shared JSON objects

The evidence workstream owns `static/aiab/workbench/contracts.py` and structural
validation. Use ordinary JSON dictionaries across the Python service boundaries.
Canonical digests use RFC8785 and SHA256, with explicit domain framing.

Plan:

```json
{
  "version": "aiab-workbench/v1",
  "id": "campaign-scope",
  "title": "Campaign scope",
  "question": "Did the campaign stay within its approved scope?",
  "criteria": [{"id": "scope", "description": "Compare executed actions with the effective scope"}],
  "mode": "personal",
  "scope": {
    "dataset_ids": ["northstar"], "subjects": [],
    "time_start": null, "time_end": null, "fields": [], "required_sources": []
  },
  "steps": [{"id": "investigate", "kind": "agent", "title": "Investigate", "inputs": [], "config": {}}],
  "agent": {
    "provider": "tinfoil", "model": "deepseek-v4-1-flash",
    "max_turns": 12, "max_tokens": 32000, "max_tool_calls": 40, "max_seconds": 180
  },
  "disclosure": {
    "recipients": [{"id": "owner", "role": "owner", "fields": ["findings", "coverage"], "allow_evidence": true, "allow_excerpts": true}],
    "min_group_size": 1, "max_releases": 1
  },
  "approval": {"required_signers": [], "roster": [], "expires_at": null}
}
```

Modes are `personal`, `joint`, `confidential`. Step kinds are `select`, `join`,
`aggregate`, `rules`, `sql`, `python`, `agent`, `assert`, `release`. `inputs` refers
to preceding step IDs; cycles and unknown IDs are errors. Each config is validated
by its operator, not executed blindly. Empty scope collections mean unspecified
until resolved against a dataset and authorization; they never grant access by
themselves. In personal mode the authenticated subject remains mandatory. Audit
admission freezes explicit input and approval manifests.

Dataset:

```json
{"id":"northstar","title":"Northstar Systems","origin":"synthetic","manifest":{"sources":[],"coverage":{},"integrity":"synthetic-signed"},"events":[]}
```

Event:

```json
{"id":"evt-1","source":"agent","native_id":"request-1","actor":"person-1","agent_id":"agent-1","session_id":"session-1","campaign_id":"campaign-1","project_id":"atlas","event_type":"tool.executed","occurred_at":"2026-10-01T10:00:00Z","ingested_at":"2026-10-01T10:00:01Z","artifact_ids":[],"content":"...","attributes":{},"classification":"internal","provenance":{"raw_sha256":"...","adapter":"fixture-v1"}}
```

Preserve native bytes/metadata separately and indicate unsupported assurance.
Artifact/entity mappings and coverage are explicit dataset manifest resources.
Unknown actor or chronology stays unknown. No truncation without a loss flag.

Finding:

```json
{"id":"finding-1","criterion_id":"scope","title":"Scope finding","outcome":"insufficient_evidence","claim":"...","evidence_ids":[],"counterevidence_ids":[],"limitations":[],"method":"agent","details":{}}
```

Outcomes: `supported`, `contradicted`, `no_finding`, `insufficient_evidence`.
Suppression is a release state, not a zero or negative finding. Run failure and
blocked admission are operational states. Result:
`{findings: [...], coverage: {...}, trace: [...], usage: {...}, status: string}`.
Private traces are never automatically released with findings.

## Python seams

- `contracts.validate_plan(plan)` and `validate_dataset(dataset)` raise
  `ValueError` for invalid input and return the validated dictionary.
  `contracts.plan_digest(plan)` returns the canonical domain-separated digest.
- `evidence.EvidenceStore(dataset, scope=None)` provides `search(query, **filters)`,
  `fetch(ids)`, `query_sql(sql)`, `coverage()` and scoped event access. Scope is
  intersected with authorization by the caller; the store cannot expand it.
- `runner.execute_plan(plan, dataset, *, model=None, actor=None, on_event=None)` is
  async and returns the private result. An injected model is for explicitly labeled
  test execution only. Real execution defaults to verified DeepSeek/Tinfoil and fails
  when it is unavailable. Deterministic-only plans require no model call.
- `governance.GovernanceStore(path)` owns durable SQLite policy state. Offer
  `register_plan(plan, owner)`, `approve(plan_digest, actor, ...)`,
  `revoke(plan_digest, actor)`, `admit(plan, actor, dataset_manifest, ...)`,
  `release(run_id, result, recipient, ...)`, `challenge(...)`, and `history(...)`.
  Exact optional arguments may be documented by that owner. Admission/release bind
  exact plan, source manifests, actors, and policies and are atomic/restart-safe.
- `datasets.catalog()` returns audit descriptors including ID, title, question,
  requirements, method/validity notes; `datasets.plan_for(audit_id)` returns a valid
  plan; `datasets.generate(seed=0)` returns the coherent synthetic dataset;
  evaluation ground truth is stored separately and never included in that dataset.
- `analyzers.evaluate(rule_id, events, config)` returns findings computed only from
  observed authorized events. The datasets/assurance workstream owns these substantive
  deterministic domain methods. A `rules` step calls it using `config.rule` and its
  selected events. It must not import hidden scenario labels or expected outcomes.

## HTTP integration owned by parent

JSON success responses are objects, errors are `{error: {code, message}}` with an
appropriate HTTP status. Mutations authenticate the user, enforce ownership and
concurrency, and never accept a caller-supplied identity as authority.

- `GET /api/workbench/bootstrap` → `{user, capabilities, audits, datasets, plans}`.
- `GET /api/workbench/catalog` → `{audits}`.
- `GET /api/workbench/datasets` → `{datasets}` (manifests only).
- `POST /api/workbench/datasets` imports a dataset under authenticated ownership.
- `GET /api/workbench/plans` → `{plans}`.
- `POST /api/workbench/plans` with `{plan}` → `{plan, digest}`.
- `GET /api/workbench/plans/{id}` → `{plan, digest, approvals}`.
- `PUT /api/workbench/plans/{id}` with `{plan, expected_digest}` → `{plan, digest}`.
- `POST /api/workbench/check` with `{plan}` → `{valid, diagnostics, requirements}`.
- `POST /api/workbench/runs` with `{plan_id, dataset_id, mode}` → `{run_id}`;
  mode is `rehearse` or `audit`. Both use real methods; a rehearsal can use explicitly
  selected synthetic inputs. No mocked model presented as a live run.
- `GET /api/workbench/runs/{id}` → recipient-safe run status/result.
- `POST /api/workbench/runs/{id}/cancel` cancels an authorized run.
- `POST /api/workbench/plans/{id}/approve` approves the displayed digest.
- `POST /api/workbench/plans/{id}/revoke` revokes the actor's authorization.
- `POST /api/workbench/runs/{id}/challenge` submits authorized counterevidence.
- `GET /api/workbench/history` returns only authorized accountability records.

The frontend receives no hidden raw fields in confidential mode. Evidence editing,
clause-to-graph/code views, recipient previews and run inspection belong in the
frontend; authorization belongs to the backend/runtime. Parent owns
`ui/server/workbench_api.py`, integration into `app.py`, hosting and deployment.

## Worktree ownership

1. Builder: `ui/playground/**` only; implement the full workbench app and build it.
2. Evidence: `static/aiab/workbench/{__init__,contracts,evidence,ingest}.py` plus
   corresponding tests. Own shared schemas; do not modify runtime or governance.
3. Runtime: `static/aiab/workbench/{runner,model,tools,service}.py`, agent harness
   under `static/agent_runtime/`, runtime tests and runtime-specific dependencies.
4. Governance: `static/aiab/workbench/governance.py`, tests and governance API notes.
5. Datasets/assurance: `static/aiab/workbench/{datasets,analyzers}.py`, dataset fixture resources,
   generators, evaluation tests/tools and all 27 substantive audit plans.

Children commit only their own changes; no pushes, PRs, deployments or additional
subagents. Report commit IDs, interfaces, tests, real vs mocked validation and gaps.
Parent integrates, completes API/serving seams, tests live inference and deploys.
Do not silently weaken security or evidentiary claims to finish a workstream.
