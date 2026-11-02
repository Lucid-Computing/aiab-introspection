# Moving the agreement UI's login from local accounts to Clerk

The agreement UI (`ui/`) signed people in against a local file of hashed passwords
(`accounts.json` on the Render disk, created once from `UI_BOOTSTRAP_ACCOUNTS`). It now
signs them in through [Clerk](https://clerk.com) when `CLERK_PUBLISHABLE_KEY` and
`CLERK_SECRET_KEY` are set, and still through the local file when they are not. This is
the plan for switching a deployment over. There are two accounts to move (the lab's and
the auditor's), so the whole thing is under an hour, most of it in the Clerk dashboard.

What stays the same: the party model (`lab` / `auditor`), the `aiab_ui` session cookie and
every route behind it, the change-log attribution by username, the Sign and key screens.
What changes: who checks the password (Clerk), where the party is stored (the Clerk user's
`public_metadata.party`), and the login page. Code: `ui/server/auth.py` (`ClerkLogin`,
`set_party`), `ui/server/app.py` (`/login`, `/logout`), `ui/server/templates/login.html`.

## 1. Create the Clerk application

Done on 2026-11-02 through Clerk's MCP server (`mcp.clerk.com`): application **AIAB
agreement UI** (`app_3LXuiIYb6yGh6zBtynTJyvhp2CG`), development instance
`ins_3LXuiDRk5zHHVDpsoAR3R3iOD7S`, publishable key
`pk_test_c3RpbGwta29pLTQ2MTQuY2xlcmsuYWNjb3VudHMuZGV2JA` (Frontend API
`still-koi-4614.clerk.accounts.dev`). What was set, and why, for the next application:

1. Sign-in: email with a code (Clerk's default) plus **username** (`auth_username`:
   used for sign-in and sign-up, required), so each person carries the name the change
   log already uses. Usernames are 4 to 64 characters.
2. **Access mode: Invite-only** (`auth_access_control.sign_up_mode = restricted`).
   Nobody can sign up unless invited, which is the property the local file had.
3. **Instance.** The dev UI runs at `aiab-ui-dev.onrender.com`, a host we do not own DNS
   for, so it uses the application's *development* instance (`pk_test_…` / `sk_test_…`):
   it accepts any origin, needs no DNS records, and is free. Its known costs: Clerk's
   development banner on the sign-in box, lower rate limits, and a cap on users, none of
   which matter at two accounts. A *production* instance (`pk_live_…`) needs the UI on a
   domain whose DNS we control (Clerk's Frontend API is served from `clerk.<domain>`);
   do that when the UI moves to a `lucidcomputing.ai` host.
4. Copy both keys from the **API keys** page.

## 2. Create the two users and bind them to their parties

Done for the first account on 2026-11-02: `jonathan` (jonathan.ng@lucidcomputing.ai,
`user_3LXupVojrCP4RZHe9XUZGm3Zrn1`), party `lab`, created without a password: the first
sign-in goes through the email code, and a password can be set afterwards. The auditor's
account is still to be created. For each person, from the dashboard's *Users* page (or
the MCP server's `create_user` and `update_user_metadata`):

1. **Invite** their email (Invite-only mode sends the invitation; they set their own
   password or use an email code). Or **Create user** directly with email and password.
2. Set the **username** to the one they had locally (`teddy`, `caleb`, as in
   `UI_BOOTSTRAP_ACCOUNTS`), so the agreements' change logs and the remembered key
   submissions keep reading as the same person. The display name is first and last name.
3. Bind the party. On a laptop with the keys in `ui/.env`:

   ```sh
   cd ui
   make party EMAIL=teddy@example.com PARTY=lab
   make party EMAIL=caleb@example.com PARTY=auditor
   ```

   (`python -m server.auth set-party`: finds the user by email through the Backend API and
   sets `public_metadata.party`. The same field can be edited by hand in the dashboard,
   *Users > the user > Metadata > Public*: `{"party": "lab"}`.)

A user with no party, or one that is not `lab` or `auditor`, is refused at login with the
exact command to run. Nothing in Clerk grants a party by default, so an invited stranger
still cannot get in.

## 3. Switch the deployment

1. On the Render service, add `CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY`. Remove
   `UI_BOOTSTRAP_ACCOUNTS` (it was first-boot only and is ignored once the keys are set).
2. Deploy this change (the dev service is image-backed: merge to `dev`, the UI image
   workflow builds and deploys it; or trigger *Build agreement UI image* by hand).
3. Check the start log says `login: Clerk (pk_…)`, then open `/login`: it shows Clerk's
   sign-in, not the username/password form.
4. Both people log in once and check: the navbar shows their name and party badge, the
   Sign screen accepts only their own party's signature, and a saved edit appears in the
   change log under the expected username.
5. Each person logs out once: they land back on the sign-in, not straight back in (the
   logout ends Clerk's browser session too).

Rollback at any point: unset the two keys and redeploy. `accounts.json` is still on the
disk and the local passwords still work.

## 4. Retire the local accounts

Once both have logged in through Clerk and nothing is left to roll back to:

1. Delete `/data/accounts.json` on the service's disk (Render shell: `rm /data/accounts.json`).
   It holds password hashes nobody needs any more.
2. Follow-up change, separate PR: remove the password path from `ui/server/auth.py`
   (`hash_password`, `verify_password`, `authenticate`, `load_accounts`, `save_account`, the
   `add` and `bootstrap` subcommands), the password form from `login.html`,
   `UI_ACCOUNTS_FILE` and `UI_BOOTSTRAP_ACCOUNTS` from `config.py`, `docker-entrypoint.sh`,
   `render.yaml` and the README, and `make account`. The tests that issue cookies through
   `web.signer.issue(Account(...))` do not change; `tests/browser/plan_chat_server.py`
   creates a local account and must switch to issuing a cookie directly.

Laptops keep working without Clerk keys until that follow-up: `make account` and the
password form are the local default. A developer who wants the real login locally puts the
development instance's keys in `ui/.env`; the development instance accepts
`http://127.0.0.1:8090`.

## Okta

Nothing in the UI changes for Okta. Clerk handles it as an enterprise connection (SAML or
OIDC to an Okta Workforce tenant, under *SSO connections* in the dashboard, bound to the
organisation's email domain): the person signs in through Okta on Clerk's sign-in box,
Clerk issues the same session token, and the UI checks it the same way. Two things to
know: Clerk creates the user at their first Okta sign-in, so either create the user with
the same email beforehand and set the party then (Clerk links by email), or set the party
right after the first sign-in, which is refused until then with the `set-party` command.
And enterprise connections are free on a development instance but a paid add-on on a
production instance.

## What to know about the design

- **Clerk authenticates; the UI's cookie authorises.** The server checks one Clerk session
  token at login and then trusts its own 12-hour cookie. It does not call Clerk on every
  request. Consequence: banning or deleting a user in Clerk locks them out when their cookie
  expires, within 12 hours. If that is ever too slow, shorten `SESSION_TTL_SECONDS` or
  re-check the Clerk session on the write routes.
- **Token check.** RS256 signature against the instance's key set from
  `https://<frontend api>/.well-known/jwks.json` (public, cached, refetched on an unknown
  key id at most once a minute), `exp` and `nbf` with 10 seconds of leeway, and `azp` (the
  origin that obtained the token) must be this UI's origin, read from the page's `Origin`
  header or `X-Forwarded-Proto` + `Host` behind Render's proxy. A token for another site
  the person is signed in to is refused.
- **The password form is refused while Clerk is on**, so a half-configured service cannot
  fall back to passwords by mistake. Without keys the start log and the login page say so.
- **No secrets in the image or the page.** The publishable key is public by design and is
  the only Clerk value the browser sees; the secret key is used server-side for the one
  user lookup and by `set-party`.
