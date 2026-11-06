# Moving the agreement UI's login from local accounts to WorkOS

The agreement UI (`ui/`) signed people in against a local file of hashed passwords
(`accounts.json` on the Render disk, filled from `UI_BOOTSTRAP_ACCOUNTS`). It now
signs them in through WorkOS AuthKit with OpenID Connect when `OIDC_ISSUER`,
`OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` are set. The local file keeps working next to
WorkOS, for demo users the operator makes on the UI side and as a way in when WorkOS is
down. This is the plan for switching a deployment over. There are two accounts to move
(the lab's and the auditor's). The WorkOS side is already done for the dev service; what
is left is the admin list, three settings on Render (set), and a deploy.

What stays the same: the party model (`lab` / `auditor`), the `aiab_ui` session cookie and
every route behind it, the change-log attribution by username, the Sign and key screens.
What changes: real people's passwords are checked by WorkOS, their party is stored in
`ui/admins.json` in the repository (admins are the lab, everyone else the auditor), and the
login page shows both ways in. Code:
`ui/server/auth.py` (`OidcLogin`, `Stamp`), `ui/server/app.py` (`/login`, `/login/start`,
`/login/callback`, `/logout`), `ui/server/templates/login.html`.

The UI speaks plain OpenID Connect. Nothing in it is WorkOS-specific: any provider with a
discovery document works (the code was written for Okta first; see the appendix), and a second
company's provider can replace or front WorkOS later with a settings change. The party
split is the UI's own, so it does not depend on how any provider models groups.

## 1. In WorkOS: the Connect application

WorkOS Connect is AuthKit acting as an OpenID Connect provider for applications of our
own. Each environment has its own AuthKit domain, which is the OIDC issuer. Lucid's
WorkOS team (*Lucid Cloud*) has a project **AIAB** with one environment, **Staging**
(a sandbox: free, not billed):

| | |
| --- | --- |
| Environment | `environment_01M6EJ1MVZ6B0QJHMQCAF6962F` (AIAB › Staging) |
| Issuer (`OIDC_ISSUER`) | `https://genuine-breeze-65-staging.authkit.app` |
| Connect application | *AIAB agreement UI*, `app_01M6FQ1CJZRVA6H5VMB12ZZM05`, OAuth, first-party, confidential |
| Client ID (`OIDC_CLIENT_ID`) | `client_01M6FQ1CJYMY24Y68WSY0SG9KP` |
| Redirect URIs | `https://aiab-ui-dev.onrender.com/login/callback`, `https://aiab-ui-latest.onrender.com/login/callback`, `http://localhost:8097/login/callback` |
| Client secret (`OIDC_CLIENT_SECRET`) | a credential of that application, made in the dashboard (shown once) |

Created on 2026-11-03 through the WorkOS management MCP server. To redo it by hand, or
for another environment: in the dashboard, pick the environment, then *Connect ›
Applications › Create application*: type **OAuth**, first-party, confidential; add the
redirect URIs (`<ui>/login/callback` for every hostname the UI answers on); then
*Credentials › Create credential* and copy the client secret. The issuer is the
environment's AuthKit domain (*Authentication › AuthKit domain*, or the `authkitDomains`
in the dashboard session). `https://<domain>/.well-known/openid-configuration` must
answer with that issuer.

Who may sign in is a WorkOS matter: anyone with a user in the environment. The Staging
environment currently **allows self sign-up** (*Authentication › Sign-up*), so anyone
can make a WorkOS account there and will get in as the auditor. Turn sign-ups off if that
is unwanted. Email verification is on: a new user's
first login asks for a code sent to their address.

Demo accounts, two ways. On the UI side, without touching WorkOS: `make account
NAME=demo PARTY=auditor` in the service's shell (or `UI_BOOTSTRAP_ACCOUNTS`, applied on every
start for names not yet in the file)
makes a local password account; it logs in through the password form under the WorkOS
button. In WorkOS: *Users › Create user* with an email and a password (or the `createUser`
operation); they are the auditor unless the email is in `admins.json`. One such user
exists already:
`jonathan.ng+aiab-demo@lucidcomputing.ai`, auditor, made for the end-to-end test.

Production: only Staging exists in the AIAB project. A production environment is a
separate WorkOS object with its own AuthKit domain, Connect application and credential,
and is billed per monthly active user (the first million are free). The `latest` Render
service points at the Staging application for now.

## 2. Switch the deployment

1. On the Render service, set `OIDC_ISSUER`, `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET`
   to the values above (done 2026-11-03). `UI_BOOTSTRAP_ACCOUNTS` holds the demo
   accounts `lab@lab.com` (lab) and `auditor@auditor.com` (auditor). `OIDC_GROUP` and `OIDC_SCOPES` stay unset: WorkOS has no groups
   claim, and the defaults fit it.
2. Say who is the lab in `ui/admins.json`, which is committed with the code and shipped
   in the image: `make admin EMAIL=teddy@lucid.example` for each person, then commit.
   Everyone else WorkOS signs in is the auditor. The email is the one WorkOS puts in the
   ID token, case does not matter. Changing the file means a commit and a deploy.
3. Deploy this change (the dev service is image-backed: merge to `dev`, the UI image
   workflow builds and deploys it; or trigger *Build agreement UI image* by hand).
4. Check the start log says `login: OpenID Connect (https://….authkit.app) and local
   accounts`, then open `/login`: it shows a *Sign in with WorkOS* button above the
   username/password form.
5. Both people log in once and check: the navbar shows their name and party badge, the
   Sign screen accepts only their own party's signature, and a saved edit appears in the
   change log under the expected username (the part of the email before the `@`).
6. Each person logs out once: they land back on the UI's login page. WorkOS keeps its
   own session, so the next *Sign in with WorkOS* signs the same person in without
   asking; see the design notes.

Rollback at any point: unset the three settings and redeploy. The local passwords never
stopped working.

## 3. Tidy the local accounts

The two people's old local accounts (`teddy`, `caleb`) stay valid after they move to
WorkOS, and anyone holding those passwords skips WorkOS. Once both have logged in through
WorkOS, delete the old entries from `/data/accounts.json` in the service's shell (keep
the file for demo accounts, or delete it if there are none: `rm /data/accounts.json`).
Local accounts are then only the demo and emergency ones the operator makes on purpose
with `make account`; each one is a password that WorkOS's policies (MFA, deactivation) do
not cover, so keep the list short and known.

Laptops work without WorkOS settings: `make account` and the password form are the local
default. A developer who wants the real login locally puts the three settings in `ui/.env`
and runs the UI on port 8097, whose redirect URI is registered (`make serve PORT=8097`;
the Makefile's default is 8090). Tested that way on 2026-11-03: sign-in with
the demo user, code exchange with PKCE and `client_secret_basic`, ID token check, cookie,
Log out.

## 4. The Clerk application

Before settling on WorkOS, a Clerk application was created to try the hosted alternative:
*AIAB agreement UI*, `app_3LXuiIYb6yGh6zBtynTJyvhp2CG`, in Jonathan's Clerk workspace,
with one user (`jonathan`). It is not used by anything. Delete it from
the Clerk dashboard once this change is merged.

## What to know about the design

- **WorkOS authenticates; the UI's cookie authorises.** The server completes one OpenID
  Connect login and then trusts its own 12-hour cookie. It does not call WorkOS on every
  request. Consequence: taking a person off `admins.json`, or deleting them in
  WorkOS, locks them out when their cookie expires, within 12 hours. If that is ever too
  slow, shorten `SESSION_TTL_SECONDS` or re-check on the write routes.
- **`admins.json` is the party split.** WorkOS says who a person is; the file says
  whether they are the lab (listed) or the auditor (everyone else). Anyone WorkOS signs
  in gets in. The UI's operator controls the split without touching WorkOS. A provider that puts groups in the ID token (Okta) can add a second
  gate with `OIDC_GROUP`; WorkOS cannot, its JWT templates only reach access tokens.
- **The flow.** Authorization code with PKCE. `/login/start` makes a state, a nonce and a
  PKCE verifier, seals them in a ten-minute signed cookie, and sends the browser to the
  provider. `/login/callback` requires the state to match that cookie, exchanges the code
  at the token endpoint with the client secret (`client_secret_basic`), and checks the ID
  token: RS256 signature against the keys the discovery document names (cached, refetched
  on an unknown key id at most once a minute), issuer, audience (the client id), expiry
  with 10 seconds of leeway, and the nonce. The discovery document must name the
  configured issuer. WorkOS's docs say PKCE is for dynamically registered clients only,
  but its authorize and token endpoints accept it from a dashboard-made confidential app
  (verified), so the UI always sends it.
- **The redirect URI** is built from the request (`X-Forwarded-Proto` and `Host` behind
  Render's proxy), so the same image serves any hostname registered on the application.
- **Local accounts work next to WorkOS.** The password form stays on the login page under
  the WorkOS button, checked against `accounts.json`. That is by choice: the operator can
  make demo users on the UI side, and can still get in if WorkOS is down. The cost is
  that each local account is a password outside WorkOS's policies, so section 3 keeps the
  list deliberate. Without the three settings the start log says `local accounts only`
  and the login page shows the form alone.
- **Log out** clears the UI cookie and, when the provider's discovery document names an
  `end_session_endpoint`, sends the browser there with the ID token (kept in its own
  HttpOnly cookie for that purpose only), then back to `/login`. **WorkOS Connect has no
  end-session endpoint**, so Log out only ends the UI session; AuthKit's own session
  stays and the next *Sign in with WorkOS* silently picks the same person. To switch
  between WorkOS accounts, use a private window, or sign out of AuthKit at the AuthKit
  domain. Okta has the endpoint and gets the full sign-out.

## Appendix: the same thing on Okta

The code was written for Okta first, and the test suite's simulated provider still has the
Okta shape (a groups claim); it was never run against a real Okta tenant. It works there with four
settings instead of three. An Okta admin makes one group (`aiab`) and one OIDC web app
integration (redirect `<ui>/login/callback`, sign-out `<ui>/login`, assigned to the
group, *Sign On › Groups claim filter*: `groups` equals `aiab`); the org URL is the
issuer. On the UI: `OIDC_ISSUER=https://<org>.okta.com`, the client ID and secret,
`OIDC_GROUP=aiab` and `OIDC_SCOPES="openid profile email groups"`. Then only members of
the group pass, before `admins.json` is consulted.

## AUTH-01/02/03 security transition (session protocol v2)

This section supersedes the earlier username attribution, redirect URI and rollback
instructions above. Session `username` is now an opaque authority identifier:
`oidc:` plus SHA-256 of the canonical JSON array `["oidc", issuer, subject]`, or
`local:` plus SHA-256 of `["local", exact_local_username]`. Email, preferred username,
name, party and client ID do not select ownership. A changed email keeps the same
OIDC authority; a different issuer or subject creates a different authority. Local
accounts remain a separate namespace even when their login name equals an OIDC ID.
`display_name` remains readable. Workbench bootstrap keeps its existing field names;
`user.username` and `user.id` carry this authority ID. Display the name, and compare
IDs for ownership and consent. Subjects inside source datasets are a separate domain;
they are not automatically relabeled to authentication IDs.

All pre-v2 `aiab_ui` cookies are rejected even with a valid signature and unexpired
expiry. Everyone must log in again. This does not revoke valid v2 sessions server-side;
the existing 12-hour expiry contract remains. Local POST `/login` requires an exact
Origin match, including scheme and authority; missing, foreign, `null` and trailing
slash Origins are rejected before credential checks or cookie issuance.
`RENDER_EXTERNAL_URL`, when set, supplies the trusted public origin ahead of caller
Host and forwarded headers, including callback/logout URLs and Secure decisions.
Without it, the existing proxy scheme/Host behavior supports local development.
All authentication cookies (pending login, UI session, ID token, and their deletion
responses) use Secure for a public HTTPS origin and HttpOnly/SameSite=Lax. Local HTTP
development remains supported. Operators must set the trusted public URL on hosted
services; proxy headers are not a substitute for that trust configuration.

### Legacy quarantine and independently verified re-enrollment

The production composition root opens **`<data>/workbench/principal-v2/`** for new
workbench/governance databases, source grants, signing material and evaluation jobs.
The old `<data>/workbench/` records are retained in place and never opened as active
authority. There is no username-to-principal fallback or automatic ownership transfer.
This intentionally quarantines all legacy ownership/enrollment, even seemingly unique
usernames: uniqueness does not establish who authenticated historically. Old receipts,
signatures, immutable revisions, runs and source records remain historical artifacts;
the server does not rewrite their bindings. Old receipt signing keys are not copied
into the new active store. Previously issued receipts retain their historical key
verification requirements; new receipts require the new store's public key.

Deployment and migration are operator decisions, not performed by this patch. Before
activating this version:

1. Stop writes and take a consistent SQLite backup (including WAL state), plus all
   workbench source files and key material. Preserve a read-only copy with file hashes.
   Inventory old identities, plans, runs, rosters, grants, releases and signing keys.
2. Verify each intended person's issuer and immutable subject independently through
   the trusted provider/operator records and their authenticated new-session ID. For
   local accounts, independently verify the named account's authorized custodian.
   Email, matching local parts, possession of an old UI cookie, or a claimant's new
   signature alone do not prove historical ownership. If ownership cannot be resolved,
   leave the records quarantined. Do not expose them to the claimant for verification.
3. In the fresh `principal-v2` store, have verified people enroll keys with the normal
   signed enrollment challenge. Trusted operators must separately verify and provision
   source/release/roster authorities using the existing offline workbench admin tooling
   with `--directory <data>/workbench/principal-v2`. Its trust acknowledgement records
   an operator assertion; it is not independent verification by the application.
4. After independently establishing rights to each input, deliberately import only
   authorized unsigned plan/data content as new objects for the verified principal.
   Rebuild source authorization, frozen rosters and release policies with current IDs;
   verify source commitments and release history/budgets, including prior releases, so
   re-enrollment does not reset a privacy limit. Obtain fresh exact-digest binding and
   consent from every participant. Never relabel signed agreements, enrollment
   challenges, approvals, receipts or historical ledger entries. Old runs/results
   remain quarantined; do not replay or serve them under a newly inferred owner.
5. Check cross-principal private reads fail, legacy cookies get 401, fresh signed
   enrollment/consent succeeds, and trusted HTTPS login sets Secure on every auth
   cookie. Keep unresolved ownership or release accounting disabled. Preserve the
   inventory, independent evidence and explicit operator decisions as migration audit
   records.

Do not roll back to the legacy username authority or point new code at an old store:
that reopens the alias defect. If activation must be paused, stop authenticated
workbench access while retaining both stores. Hosted deployment, provider validation,
legacy verification and privacy-accounting reconciliation remain gates for the parent
operator; no production migration or provider changes were performed here.
