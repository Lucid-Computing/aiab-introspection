# October 27 demo verification

The October 26 demo exposed three separate problems: stale deployment tracking,
failed executions presented as clear verdicts, and classifier execution failures.
The fixes are on master (`de08054`, `7732e2a`, `76456eb`).

## Changes

- Deployment tracking follows the plan PR's merge commit and distinguishes a
  superseded deployment from a pending one. A selected plan is only labelled
  deployed when its digest matches the live box; an unsuccessful deployment can
  be retried.
- Runs fetch the current production snapshot, including trusted signing keys,
  without restarting the website. A run retains that snapshot throughout execution.
- Execution status, audit verdict and report verification are separate. A signed,
  verified failure report has **no verdict**. This also fixes historical run pages.
- Worker/model failures expose bounded error codes and diagnostic guidance without
  releasing prompts, logs or arbitrary exception text.
- The classifier uses low reasoning effort within its existing signed 512-token
  output limit. A live attested-router probe reproduced truncation with the default
  effort and a valid JSON score with low effort. This probe used the router, not
  the pinned inference host; the original generic failure does not establish its
  exact cause retrospectively. The full rehearsals below exercise the pinned host.

## First full rehearsal

Created `demo-capybara-rehearsal-20261027` through the website. Downloaded and
compared its code/prompts/config against the reviewed policy, computed the digest
locally, signed with both currently trusted keys, uploaded both signatures, and
requested deployment. [Plan PR #16](https://github.com/Lucid-Computing/aiab-introspection/pull/16)
and [release](https://github.com/Lucid-Computing/aiab-introspection/actions/runs/40462311705)
completed, producing v0.2.12.

- [Benign run](https://aiab-ui-latest.onrender.com/runs/20261027T064213Z-e886b2): completed, not flagged.
- [Capybara run](https://aiab-ui-latest.onrender.com/runs/20261027T064251Z-d09217): completed, flagged.
- Both reports verified locally outside Render, against a freshly attested identity,
  the jointly signed plan and input commitments recomputed from selected proxy records.
- Flipping either report's verdict caused `invalid_signature`.
- The [original failed run](https://aiab-ui-latest.onrender.com/runs/20261026T093728Z-aa5894)
  now displays “Execution failed — no verdict”.

The cases are synthetic public demonstration prompts sent through the real test
proxy. The inference and box are real. The export is still signed by the demo
logger adapter; this is not native logger/WORM provenance verification. One
operator exercised both demo signing roles. Two correct cases establish a working
workflow, not classifier accuracy on general data.

## Checks

600 static tests and 38 UI tests passed; Ruff passed. After the final low-effort
change, all 10 model adapter tests passed and the reviewed-image CI build passed.

## Recorded repeat

A second fresh agreement, `demo-capybara-video-20261027`, repeated the process via
[PR #17](https://github.com/Lucid-Computing/aiab-introspection/pull/17) and
[the release workflow](https://github.com/Lucid-Computing/aiab-introspection/actions/runs/40462913210).
The video includes locally generated plan signatures, deployment, both audit
outcomes, the historical failure display, and independent verification.

- [Watch the captioned video](https://files.jonathan.quest/demo-videos/full-demo-20261027.html)
  (existing files-server login required; build wait accelerated 12×).
- [Benign run](https://aiab-ui-latest.onrender.com/runs/20261027T064819Z-ce8b92).
- [Flagged run](https://aiab-ui-latest.onrender.com/runs/20261027T064903Z-f0db39).

The downloaded reports, locally attested identity anchors and recomputed input
commitments are retained privately on the operator's machine. Signing keys were
kept outside the repository and do not appear in the video.
