# ui/playground: audit plan playground (prototype)

A standalone editor for trying out a new shape of audit plan: a graph of five block types
(Input Logs, Classifier, Run Python, Run LLM, Final Report) that can be edited as a graph or
as YAML. It is an exploration, not part of the agreement flow: it does not read or write
agreements, call any server endpoint, run Python, call a model, or sign anything. Plans
live in the browser (localStorage) and move between browsers by Export / Import.

## Run it

```sh
cd ui/playground
pnpm install
pnpm dev            # http://localhost:5174/audit-plan-playground/
```

Or build it and let the agreement UI serve it behind its login:

```sh
pnpm build          # writes dist/ (git-ignored)
cd .. && make dev   # http://127.0.0.1:8090/audit-plan-playground/
```

Checks: `pnpm test` (Vitest, the plan layer) and `pnpm typecheck`.

## How it is built

One canonical **Plan IR** (`src/plan/types.ts`, Zod schemas and TypeScript types). The graph
and the YAML are two projections of it:

| File | What it is |
| --- | --- |
| `src/plan/types.ts` | The IR: plan, five node types, their configs. Canvas positions (`Layout`) are separate. |
| `src/plan/ports.ts` | Typed ports: each node's output, which inputs it takes, the one compatibility table. |
| `src/plan/validate.ts` | The gate into the IR: schema, unique ids, references that resolve, no cycles. |
| `src/plan/lint.ts` | Problems in a valid plan (type mismatches, missing inputs, report fields that do not resolve). Shown, not blocking. |
| `src/plan/yaml.ts` | IR ↔ YAML, with errors mapped to line numbers; export/import with a separate `layout:` section. |
| `src/plan/graph.ts` | IR ↔ React Flow nodes and edges. Each input binding is one edge. |
| `src/plan/ops.ts` | Graph and inspector edits: add, delete, connect (refuses cycles and type mismatches), rename. |
| `src/plan/report.ts` | What a Final Report field may read: declared Value fields, or aggregates of classifications. |
| `src/plan/simulate.ts` | Deterministic mock run over synthetic logs. Nothing is executed. |
| `src/plan/mockLogs.ts`, `samples.ts` | Synthetic log sources and the three example plans (written in the YAML DSL). |
| `src/state/store.ts` | Editor state: the last valid plan, layout, the code text, persistence. |
| `src/components/` | Toolbar (`App.tsx`), palette, canvas, node view, inspector, code pane, simulate dialog. |

Connections are input bindings on the consuming node, `inputs: { name: "<node>.<output>" }`,
so one output can feed several nodes and Run Python / Run LLM can take several named inputs.
The Final Report is a sink with exactly one input, `result`; its fields are the shape it
releases, each read from that input. Code edits that do not parse, fail the schema, or break the graph (unknown
ids, dangling references, cycles) are not applied: the graph keeps the last valid plan and
the code pane lists the errors by line.

The styles are the agreement UI's own `ui/assets/swiss.css` and `aiab.css`, imported
directly.
