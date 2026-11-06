# Workbench authorization test report

Date: 2026-11-06. Tester: Sol, isolated worktree. Local baseline: `f3aec71`.
Hosted target: `https://aiab-dev-astra-one.onrender.com`, deployed `e404703`
(per task context; deployment SHA was not independently attested).

Scope: WorkOS/public login, sessions/CSRF, private ownership, exact-version consent,
revocation, recipient release accounting, challenges/receipts and source drilldown.
Read `WORKBENCH_DEMO.md`, `WORKBENCH_IMPLEMENTATION_CONTRACT.md`, OIDC migration notes,
HTTP integration, auth, store, governance and runtime authorization code.
No application fixes, provider changes, real users, inference, service changes,
PRs or pushes. Hosted requests were login/read and a nonpersisting validation probe;
no hosted objects were created or modified. Credentials were read only inside a script;
credentials, cookie values and provider payloads are excluded from this report.

## Prioritized findings

### AUTH-01 — High: independent identities alias one private workbench actor

Paths: `ui/server/auth.py:354`, `:366`; `ui/server/workbench_api.py:83`;
`ui/server/workbench_store.py:135` (owner-filtered plan lookup).

Reproduction (local, synthetic):
1. Map OIDC claims `{sub: subject-owner, email: tester-local@one.example}` through
   production `OidcLogin.account()` and issue the production session cookie.
2. Save a fresh UUID-named private plan with same-origin POST.
3. Map `{sub: subject-other, email: tester-local@two.example}` and replace the
   session, then GET that plan. Alternatively authenticate the separate local
   password account named `tester-local` and use its session.

Expected: independent principals cannot read the private plan (404).
Actual: both variants return 200 and the owner's plan. Distinct `sub` values and
email domains are discarded; both OIDC identities and the local account become
`tester-local`. Workbench authority is keyed only by this username, regardless of
party. This defeats the ownership predicate before governance can distinguish users.
The same alias is used for other private objects and consent identities; this test
proves plan disclosure, not a hosted victim compromise or signature forgery.

Evidence: `test_independent_principals_cannot_read_each_others_private_plan`, two
cases, assertion `Independent ... reads private owner plan: HTTP 200` (`200 != 404`).
Action: bind OIDC principals to stable issuer+subject identities, namespace local
accounts, and explicitly migrate existing ownership and enrollment bindings. Preserve
human-readable usernames as display metadata. Do not silently relabel existing keys.

### AUTH-04 — Medium: a reader's private copy hides another owner's agreement

Paths: `ui/server/workbench_store.py:97` (global digest owner-count check),
`ui/server/workbench_api.py:101`, `:128`, `:368`.

Reproduction: use the local `joint` fixture, which provisions synthetic source/release
policy and shares Alice's bound agreement with Bob and recipient Reader. Bob's GET
`/api/workbench/plans/{id}` initially returns 200. Reader POSTs the identical plan
as a private plan under Reader's own account (200). Bob repeats GET: 404.

Expected: saving a private copy cannot change another owner's sharing or approval
access. Actual: `current_owner(digest)` now finds two owners and discovery drops
Alice's agreement for other participants. Run visibility also uses this discovery
path. No privileged policy operation or signature is needed. The observed effect is
agreement disappearance; downstream run denial follows from source inspection.

Evidence: `test_reader_copy_cannot_hide_owner_agreement_from_other_participants`,
assertion `Reader's private copy hid owner's agreement: HTTP 404` (`404 != 200`).
Action: resolve currentness by the governance record's owner and exact digest rather
than requiring a globally unique private-plan digest.

### AUTH-02 — Medium: password login permits cross-site session replacement

Path: `ui/server/app.py:394` through `:408`.

Reproduction: POST a standard URL-encoded `/login` form containing credentials for a
synthetic attacker-controlled local account and `next=/workbench`, with
`Origin: https://attacker.example` (also reproduced without Origin). Start with a
synthetic victim session in the client.

Expected: reject the cross-site login without issuing a replacement session.
Actual: 303 plus a new `aiab_ui` cookie for the submitted account. The login route
never validates Origin or a CSRF token. An attacker who knows a permitted local
account can direct a victim into that account, risking subsequent work being saved
under attacker-accessible ownership. Workbench JSON Origin checks do not protect the
login form. SameSite controls sending existing cookies; the login needs none.

Evidence: `test_login_rejects_cross_site_session_replacement`, two cases,
assertion `Cross-site login replaced browser identity: HTTP 303` (`303 != 403`).
Action: protect password login with CSRF validation, including exact public Origin
validation or an equivalent login token mechanism. Browser exploitation was not
attempted; this is a local HTTP reproduction using production endpoint code.

### AUTH-03 — Low: HTTPS authentication cookies omit Secure

Paths: `ui/server/app.py:352`, `:389`, `:390`, `:408`.

Reproduction: GET hosted `/login/start`; POST hosted `/login` with the dedicated
local demo credentials and same-origin Origin. Inspect attributes without recording
values. Repeat password login in the local HTTPS TestClient.

Expected: hosted HTTPS authentication cookies carry `Secure` and `HttpOnly`.
Actual: `aiab_login` and `aiab_ui` have `HttpOnly; SameSite=lax` but no `Secure`.
The ID-token cookie also omits Secure by source inspection; real OIDC callback was
not completed. HTTP transmission could expose cookies before an HTTP redirect;
actual exploitability depends on browser HSTS/HTTPS enforcement, which was not tested.

Evidence: hosted sanitized flags `(Secure=False, HttpOnly=True, SameSite=lax=True)`
for both observed cookies; `test_https_session_cookie_is_secure` fails its attribute
assertion. Action: set Secure on deployed authentication cookies, with an explicit
local HTTP development exception if needed.

## Passed boundaries and documented gates

Hosted benign HTTP observations:
- `/login`: 200, WorkOS sign-in offered. `/login/start`: 303 to the configured
  AuthKit host, redirect URI exactly the isolated service's `/login/callback`,
  PKCE S256, state and nonce present. This checks the app's outbound configuration,
  not the provider's full allowlist or successful real WorkOS authentication.
- Unauthenticated bootstrap: 401. Local demo login: 303 to `/workbench`;
  authenticated bootstrap: 200 with a user; `/workbench`: 303 to the frontend.
- POST `/api/workbench/check` with an empty body: absent/foreign Origin gives
  403 `origin_denied`; same-origin reaches validation and returns 404 `not_found`.
  No persisted plan or inference was requested.

Local positive controls and existing integration coverage:
- Workbench mutation Origin must match exactly; missing, null, alternate scheme,
  alternate host and trailing-slash variants fail closed. Configured hosted Origin
  cannot be overridden with caller-supplied forwarded headers.
- Distinct synthetic usernames cannot fetch another user's private plan/run or
  cancel it. The AUTH-01 mapping defect occurs before these otherwise working checks.
- Signed enrollment challenge replay, immutable keys, approval replay, stale
  digest approval, and old-release access after plan edits are rejected.
- Source grant removal and consent revocation stop execution at the callback and
  block new release. The tester's injected runtime invokes the production API's
  independent `authorize()` callback: after Bob's revocation it never reaches its
  next work marker, the run fails, and no result is stored. Real runtime integration
  includes a separate denial test after a tool result before further model work
  with a labeled inference double; that case passed after the local runtime build
  (see validation history below). No real inference was performed.
- Repeated recipient release returns one identical signed package, with one stored
  recipient release. A distinct admission request cannot bypass the same population's
  release reservation. Unauthorized recipients get 404. Reservations are deliberately
  conservative; unused/failed reservations are not proven reusable.
- Projection excludes seeded private trace/usage/raw/details/evidence IDs; source
  drilldown requires owner, synthetic rehearsal, scope and a cited reference. Audit
  drilldown refuses access, even for the owner. Findings' permitted free text remains
  releasable; these checks are not a semantic DLP guarantee for model-written claims.
- Receipt projection/signature tampering and mismatched expected recipient, plan or
  run fail verification. Challenge parent substitution, visibility expansion,
  conflicted disposition and recipient reference attachment are rejected. Previously
  released findings remain challengeable after withdrawal as documented.

Documented behavior, not new bugs: Render confidential admission refuses with
`sealed_runtime_required`; caller-supplied runtime facts cannot override it.
Imported synthetic labels do not bypass consent. Audit evidence flags are upper
bounds; reference delivery is not implemented. AuthKit's provider session survives
UI logout because it has no end-session endpoint, as the migration notes explain.
The local session is stateless with a 12-hour signed expiry; cookie deletion is not
server-side revocation. GET completed audit status currently creates the first
recipient release; do not interpret a status read as a private execution-only read.
No external receipt-key trust, ledger checkpoint witnessing or confidential host
attestation was established by these tests.

## Reproduction and validation

Owned test file: `ui/tests/test_tester_workbench_authorization.py`. All hostile
cases use disposable local SQLite stores and synthetic keys/actors. Login endpoints
are mounted without application lifespan to avoid production background jobs.
Identity collision probes directly use production claim mapping/session issuance;
they do not bypass the mapping under test or contact a provider. Existing OIDC tests
separately cover signed tokens, issuer/audience/expiry/nonce and state.

```sh
PYTHONPATH=ui uv run --project ui pytest -q ui/tests/test_tester_workbench_authorization.py -rx
# Reveal the six preserved failing assertions for four findings:
PYTHONPATH=ui uv run --project ui pytest -q ui/tests/test_tester_workbench_authorization.py --runxfail -k 'independent_principals or login_rejects or https_session or reader_copy'
PYTHONPATH=ui uv run --project ui pytest -q ui/tests/test_workbench_api.py ui/tests/test_oidc_login.py
uv run --project static pytest -q static/tests/test_workbench_governance.py static/tests/workbench/test_runtime_integration.py
PYTHONPATH=ui uv run --project ui ruff check ui/tests/test_tester_workbench_authorization.py
```

Known defects are `xfail(strict=True, raises=AssertionError)`: the suite preserves
expected secure behavior and fails on unexpected passes after a fix. The explicit
`--runxfail` reproduction produced six assertion failures, eight deselected (exit 1),
all matching the observations above. Normal-suite totals are recorded below.

Validation results:
- Final owned tests: **8 passed, 6 xfailed** (96.26 seconds).
- Combined owned tests (before AUTH-04 was added), existing workbench API and OIDC
  tests: **38 passed, 5 xfailed** (224.22 seconds). Thus all 30 existing UI tests
  passed. The final AUTH-04 case was independently reproduced and included in the
  final owned-suite result above.
- Ruff on the owned file: passed.
- Initial static combined run: **72 passed, 9 failed** (278.34 seconds). Eight
  agent-dependent cases failed at `runner.py:328` with `pi_runtime_not_built`;
  the cancellation case timed out waiting for the same unbuilt runtime to start.
  Evidence: `test_reauthorization_independent_of_private_events` expected
  `authorization_revoked`, actual `pi_runtime_not_built`; the other affected cases
  were scoped multiturn, graph/subject denial, findings context, native snapshot,
  three resource budgets and deadline/cancellation. These are local setup failures,
  not demonstrated product authorization bypasses. The isolated checkout initially
  lacked both `static/agent_runtime/isolate.node` and its Node dependencies.
  Following `static/agent_runtime/README.md`, installed locked dependencies with
  `npm ci --prefix static/agent_runtime --ignore-scripts` and compiled with
  `make -C static/agent_runtime`, only inside this assigned worktree.
- After the local build, reran the nine failed cases with the same static command
  plus `--lf`: **9 passed, 34 deselected** (62.20 seconds). Together with the initial
  72 passes, all 81 selected static cases passed across the two runs. This is a
  targeted rerun, not a claim of a fresh 81-case full green execution.
- No frontend changes were made; no frontend build/browser checks were run.
