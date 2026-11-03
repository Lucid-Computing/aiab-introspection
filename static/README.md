> This is the README of the static part. Run every command below from `static/`.
> Repository overview: [../README.md](../README.md).

# The static part: the box and the auditor tool

The box is the code that runs inside Tinfoil's confidential VM. It receives a set of
records and a plan signed by the lab and the auditor, checks both, runs the plan with the
pinned model, and returns a signed report. The image holds no plan: every run carries its
own. The auditor tool is the client that attests the box, submits a run and verifies the
report offline. The agreement UI (`../ui/`) imports both so it can never disagree with them.

What is here:

| Folder | What |
| --- | --- |
| `aiab/baked/` | the box: `plan_ir.py` (the plan's rules), `graph.py` (how each block runs), `prompts.py` (what the model is asked), `output.py` (what may leave), `worker.py` (the sandbox), `records.py` (the submission and the input scope), `bundle.py` (digest and signatures), `runtime.py` and `main.py` (the service), `trust.json` (the public keys and the pinned model) |
| `aiab/audit/` | shared pieces: the wire protocol and signatures, the report signer, the model client, the HTTP routes |
| `auditor/` | the auditor tool: `baked.py` is the command the UI's Runs page calls step by step |
| `deploy/` | the Dockerfile, the reproducible-build script, and `tinfoil/audit/` with the release script and the release pins |
| `demo/audit/` | the walkthrough script the Runs page mirrors, the demo logger stand-in, and evidence from past releases |
| `tests/` | pytest; `tests/fixtures/graph-plan/` is a signed example plan with transcripts |

The plan format and what each block may do: [../docs/PLAN_SPEC.md](../docs/PLAN_SPEC.md).
Branches, environments and releases: [../docs/BRANCHES_AND_RELEASE.md](../docs/BRANCHES_AND_RELEASE.md).

## Development

Python 3.12 and `uv`. `make sync` installs the locked environment; `make check` runs Ruff
and pytest. `make image` builds the box image as `aiab:local` from the repository root;
`make image-reproducible` compares two builds. The box starts with
`python -m aiab.baked.main`; `python -m auditor.baked --help` lists the auditor commands.
