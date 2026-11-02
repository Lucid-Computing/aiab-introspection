# Moving the agreement UI's login from local accounts to Okta

The agreement UI (`ui/`) signed people in against a local file of hashed passwords
(`accounts.json` on the Render disk, created once from `UI_BOOTSTRAP_ACCOUNTS`). It now
signs them in through Okta with OpenID Connect when `OIDC_ISSUER`, `OIDC_CLIENT_ID` and
`OIDC_CLIENT_SECRET` are set, and still through the local file when they are not. This is
the plan for switching a deployment over. There are two accounts to move (the lab's and
the auditor's). The work is one app integration and two groups in Okta, three settings on
Render, and a deploy.

What stays the same: the party model (`lab` / `auditor`), the `aiab_ui` session cookie and
every route behind it, the change-log attribution by username, the Sign and key screens.
What changes: who checks the password (Okta), where the party is stored (an Okta group),
and the login page. Code: `ui/server/auth.py` (`OidcLogin`, `Stamp`), `ui/server/app.py`
(`/login`, `/login/start`, `/login/callback`, `/logout`),
`ui/server/templates/login.html`.

The UI speaks plain OpenID Connect. Nothing in it is Okta-specific: any provider with a
discovery document and a groups claim works (Auth0, Entra ID, Keycloak), and a second
company's provider can replace or front Okta later with a settings change.

## 1. In Okta: the app integration and the two groups

An Okta administrator does this, about ten minutes.

1. **Groups.** *Directory > Groups > Add group*: `aiab-lab` and `aiab-auditor`. These names
   are the defaults the UI expects (`OIDC_GROUP_LAB`, `OIDC_GROUP_AUDITOR` change them).
   Put each person in exactly one. A person in neither, or in both, is refused at login
   with a message naming the groups.
2. **App integration.** *Applications > Create App Integration*: sign-in method **OIDC -
   OpenID Connect**, application type **Web Application**. Grant type: Authorization Code
   (default). Sign-in redirect URIs: `https://aiab-ui-dev.onrender.com/login/callback`
   (add `http://127.0.0.1:8090/login/callback` for laptops). Sign-out redirect URIs:
   `https://aiab-ui-dev.onrender.com/login` (and the laptop one), so Log out can end the
   Okta session and come back. Assignments: the two groups.
3. **Groups claim.** The ID token must carry the groups. On the app integration's
   *Sign On* tab, *OpenID Connect ID Token > Groups claim type: Filter*, *Groups claim
   filter*: `groups` *Starts with* `aiab-`. The UI asks for the `groups` scope, which the
   org authorization server honours only with this filter set.
4. Copy the **Client ID** and **Client secret** from the *General* tab. The issuer is the
   Okta org URL, `https://<org>.okta.com` (no path). If the org uses a custom
   authorization server instead, the issuer is `https://<org>.okta.com/oauth2/<id>` and
   the groups claim is added on that server's *Claims* tab rather than on the app.

Demo accounts: an Okta admin can create users by hand with any email and a set password
(*Directory > People > Add person*, *Set by admin*). Put them in a group and assign the
app. They are ordinary Okta users for billing.

## 2. Switch the deployment

1. On the Render service, add `OIDC_ISSUER`, `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET`.
   Remove `UI_BOOTSTRAP_ACCOUNTS` (it was first-boot only and is ignored once the three
   are set).
2. Deploy this change (the dev service is image-backed: merge to `dev`, the UI image
   workflow builds and deploys it; or trigger *Build agreement UI image* by hand).
3. Check the start log says `login: OpenID Connect (https://…okta.com)`, then open
   `/login`: it shows a *Sign in with Okta* button, not the username/password form.
4. Both people log in once and check: the navbar shows their name and party badge, the
   Sign screen accepts only their own party's signature, and a saved edit appears in the
   change log under the expected username (the part of the Okta login before the `@`).
5. Each person logs out once: they land on Okta's sign-out and back on the UI's login,
   and the next *Sign in with Okta* asks who they are.

Rollback at any point: unset the three settings and redeploy. `accounts.json` is still on
the disk and the local passwords still work.

## 3. Retire the local accounts

Once both have logged in through Okta and nothing is left to roll back to:

1. Delete `/data/accounts.json` on the service's disk (Render shell: `rm /data/accounts.json`).
   It holds password hashes nobody needs any more.
2. Follow-up change, separate PR: remove the password path from `ui/server/auth.py`
   (`hash_password`, `verify_password`, `authenticate`, `load_accounts`, `save_account`, the
   `add` and `bootstrap` subcommands), the password form from `login.html`,
   `UI_ACCOUNTS_FILE` and `UI_BOOTSTRAP_ACCOUNTS` from `config.py`, `docker-entrypoint.sh`,
   `render.yaml` and the README, and `make account`. The tests that issue cookies through
   `web.signer.issue(Account(...))` do not change; `tests/browser/plan_chat_server.py`
   creates a local account and must switch to issuing a cookie directly.

Laptops keep working without Okta settings until that follow-up: `make account` and the
password form are the local default. A developer who wants the real login locally puts the
three settings in `ui/.env` and has the laptop redirect URI on the app integration.

## 4. The Clerk application

Before settling on Okta, a Clerk application was created to try the hosted alternative:
*AIAB agreement UI*, `app_3LXuiIYb6yGh6zBtynTJyvhp2CG`, in Jonathan's Clerk workspace,
with two users (`jonathan`, `aiab-e2e-auditor`). It is not used by anything. Delete it from
the Clerk dashboard once this change is merged.

## What to know about the design

- **Okta authenticates; the UI's cookie authorises.** The server completes one OpenID
  Connect login and then trusts its own 12-hour cookie. It does not call Okta on every
  request. Consequence: removing a person from the group, or deactivating them in Okta,
  locks them out when their cookie expires, within 12 hours. If that is ever too slow,
  shorten `SESSION_TTL_SECONDS` or re-check the group on the write routes.
- **The flow.** Authorization code with PKCE. `/login/start` makes a state, a nonce and a
  PKCE verifier, seals them in a ten-minute signed cookie, and sends the browser to Okta.
  `/login/callback` requires the state to match that cookie, exchanges the code at the
  token endpoint with the client secret, and checks the ID token: RS256 signature against
  the keys the discovery document names (cached, refetched on an unknown key id at most
  once a minute), issuer, audience (the client id), expiry with 10 seconds of leeway, and
  the nonce. The discovery document must name the configured issuer.
- **The redirect URI** is built from the request (`X-Forwarded-Proto` and `Host` behind
  Render's proxy), so the same image serves any hostname registered on the app integration.
- **The password form is refused while Okta is on**, so a half-configured service cannot
  fall back to passwords by mistake. Without the settings the start log and the login page
  say so.
- **Log out** clears the UI cookie and sends the browser to Okta's end-session endpoint
  with the ID token (kept in its own HttpOnly cookie for that purpose only), then back to
  `/login`. Without that, Okta's own session would sign the same person straight back in,
  which matters when switching between demo accounts.
