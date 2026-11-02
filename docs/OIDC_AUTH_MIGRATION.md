# Moving the agreement UI's login from local accounts to Okta

The agreement UI (`ui/`) signed people in against a local file of hashed passwords
(`accounts.json` on the Render disk, created once from `UI_BOOTSTRAP_ACCOUNTS`). It now
signs them in through Okta with OpenID Connect when `OIDC_ISSUER`, `OIDC_CLIENT_ID` and
`OIDC_CLIENT_SECRET` are set. The local file keeps working next to Okta, for demo users
the operator makes on the UI side and as a way in when Okta is down. This is the plan for
switching a deployment over. There are two accounts to move (the lab's and the
auditor's). The work is one app integration and one group in Okta, three settings and two
party entries on Render, and a deploy.

What stays the same: the party model (`lab` / `auditor`), the `aiab_ui` session cookie and
every route behind it, the change-log attribution by username, the Sign and key screens.
What changes: real people's passwords are checked by Okta, their party is stored in
`parties.json` on the UI's disk, and the login page shows both ways in. Code: `ui/server/auth.py` (`OidcLogin`, `Stamp`), `ui/server/app.py`
(`/login`, `/login/start`, `/login/callback`, `/logout`),
`ui/server/templates/login.html`.

The UI speaks plain OpenID Connect. Nothing in it is Okta-specific: any provider with a
discovery document works (Auth0, Entra ID, Keycloak), and a second company's provider can
replace or front Okta later with a settings change. The party split is the UI's own, so it
does not depend on how any provider models groups.

## 1. In Okta: the app integration and the group

An Okta administrator does this, about ten minutes.

1. **Group.** *Directory > Groups > Add group*: `aiab`. This is the one group that may use
   the UI (`OIDC_GROUP` renames it; empty turns the check off). Put every person who
   should get in, real or demo, in it. Lab or auditor is not an Okta matter: see step 2 of
   the next section.
2. **App integration.** *Applications > Create App Integration*: sign-in method **OIDC -
   OpenID Connect**, application type **Web Application**. Grant type: Authorization Code
   (default). Sign-in redirect URIs: `https://aiab-ui-dev.onrender.com/login/callback`
   (add `http://127.0.0.1:8090/login/callback` for laptops). Sign-out redirect URIs:
   `https://aiab-ui-dev.onrender.com/login` (and the laptop one), so Log out can end the
   Okta session and come back. Assignments: the `aiab` group.
3. **Groups claim.** The ID token must carry the group. On the app integration's
   *Sign On* tab, *OpenID Connect ID Token > Groups claim type: Filter*, *Groups claim
   filter*: `groups` *Equals* `aiab`. The UI asks for the `groups` scope, which the org
   authorization server honours only with this filter set.
4. Copy the **Client ID** and **Client secret** from the *General* tab. The issuer is the
   Okta org URL, `https://<org>.okta.com` (no path). If the org uses a custom
   authorization server instead, the issuer is `https://<org>.okta.com/oauth2/<id>`, the
   groups claim is added on that server's *Claims* tab rather than on the app, and a
   `groups` scope must exist on its *Scopes* tab, otherwise the login fails with
   `invalid_scope`.

Demo accounts, two ways. On the UI side, without touching Okta: `make account
NAME=demo PARTY=auditor` in the service's shell (or `UI_BOOTSTRAP_ACCOUNTS` on first boot)
makes a local password account; it logs in through the password form under the Okta
button. In Okta: an admin creates a user by hand with any email and a set password
(*Directory > People > Add person*, *Set by admin*), puts them in the group, assigns the
app, and adds the email to `parties.json`. Those are ordinary Okta users for billing.

## 2. Switch the deployment

1. On the Render service, add `OIDC_ISSUER`, `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET`.
   `UI_BOOTSTRAP_ACCOUNTS` can go (it was first-boot only; the accounts file exists).
2. Say who is which party. Either `UI_PARTIES="teddy@lucid.example:lab,caleb@other.example:auditor"`
   on the service (applied on every start), or after the deploy, in the service's shell,
   `python -m server.auth party teddy@lucid.example lab` for each person. Both write
   `/data/parties.json`; a change applies at the next login. The email is the one Okta
   puts in the ID token, case does not matter. A person in the Okta group but not in the
   file is refused with the exact command to run.
3. Deploy this change (the dev service is image-backed: merge to `dev`, the UI image
   workflow builds and deploys it; or trigger *Build agreement UI image* by hand).
4. Check the start log says `login: OpenID Connect (https://…okta.com) and local
   accounts`, then open `/login`: it shows a *Sign in with Okta* button above the
   username/password form.
5. Both people log in once and check: the navbar shows their name and party badge, the
   Sign screen accepts only their own party's signature, and a saved edit appears in the
   change log under the expected username (the part of the Okta login before the `@`).
6. Each person logs out once: they land on Okta's sign-out and back on the UI's login,
   and the next *Sign in with Okta* asks who they are.

Rollback at any point: unset the three settings and redeploy. The local passwords never
stopped working.

## 3. Tidy the local accounts

The two people's old local accounts (`teddy`, `caleb`) stay valid after they move to Okta,
and anyone holding those passwords skips Okta. Once both have logged in through Okta,
delete the old entries from `/data/accounts.json` in the service's shell (keep the file
for demo accounts, or delete it if there are none: `rm /data/accounts.json`). Local
accounts are then only the demo and emergency ones the operator makes on purpose with
`make account`; each one is a password that Okta's policies (MFA, deactivation) do not
cover, so keep the list short and known.

Laptops work without Okta settings: `make account` and the password form are the local
default. A developer who wants the real login locally puts the three settings in `ui/.env`
and has the laptop redirect URI on the app integration.

## 4. The Clerk application

Before settling on Okta, a Clerk application was created to try the hosted alternative:
*AIAB agreement UI*, `app_3LXuiIYb6yGh6zBtynTJyvhp2CG`, in Jonathan's Clerk workspace,
with one user (`jonathan`). It is not used by anything. Delete it from
the Clerk dashboard once this change is merged.

## What to know about the design

- **Okta authenticates; the UI's cookie authorises.** The server completes one OpenID
  Connect login and then trusts its own 12-hour cookie. It does not call Okta on every
  request. Consequence: removing a person from the group or from `parties.json`, or
  deactivating them in Okta, locks them out when their cookie expires, within 12 hours. If
  that is ever too slow, shorten `SESSION_TTL_SECONDS` or re-check on the write routes.
- **Access and party are two separate checks.** The Okta group says who may use the UI
  at all, and an Okta admin controls it. `parties.json` says which side each email is on,
  and the UI's operator controls it without touching Okta. An email in the file but not
  in the group gets nowhere; an email in the group but not in the file is told to ask.
- **The flow.** Authorization code with PKCE. `/login/start` makes a state, a nonce and a
  PKCE verifier, seals them in a ten-minute signed cookie, and sends the browser to Okta.
  `/login/callback` requires the state to match that cookie, exchanges the code at the
  token endpoint with the client secret, and checks the ID token: RS256 signature against
  the keys the discovery document names (cached, refetched on an unknown key id at most
  once a minute), issuer, audience (the client id), expiry with 10 seconds of leeway, and
  the nonce. The discovery document must name the configured issuer.
- **The redirect URI** is built from the request (`X-Forwarded-Proto` and `Host` behind
  Render's proxy), so the same image serves any hostname registered on the app integration.
- **Local accounts work next to Okta.** The password form stays on the login page under
  the Okta button, checked against `accounts.json`. That is by choice: the operator can
  make demo users on the UI side without an Okta admin, and can still get in if Okta is
  down. The cost is that each local account is a password outside Okta's policies, so
  section 3 keeps the list deliberate. Without the three settings the start log says
  `local accounts only` and the login page shows the form alone.
- **Log out** clears the UI cookie and sends the browser to Okta's end-session endpoint
  with the ID token (kept in its own HttpOnly cookie for that purpose only), then back to
  `/login`. Without that, Okta's own session would sign the same person straight back in,
  which matters when switching between demo accounts.
