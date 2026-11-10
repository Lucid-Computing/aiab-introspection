# How to run an audit from the UI

This procedure uses the agreement UI against the live box. For the command-line route, see [BAKED_PLAN_OPERATIONS.md](BAKED_PLAN_OPERATIONS.md).

## Before you start

- You have a login. The lab account and the auditor account are different people.
- The UI server holds the lab signing key in `AIAB_LAB_KEY` and its certificate in `AIAB_LAB_CERTIFICATE`. The server then signs each version as the lab.
- The auditor can sign. On the prototype the UI server holds the auditor signing key in `AIAB_AUDITOR_KEY` and its certificate in `AIAB_AUDITOR_CERTIFICATE`, and Sign signs on the server. Without them, the auditor sets up signing in the browser first (see "Set up signing"). [THREAT_MODEL.md](THREAT_MODEL.md), "Prototype shortcut", says what this shortcut gives up.
- The UI server holds `lab.token` and `logger.key` in `AIAB_DEMO_KEYS`. The box operator supplies them.
- The release pins in the checkout match the release the box runs. If they do not, step 2 of the run refuses.
- The Logs page shows at least one recorded session. Without a session, the run cannot start.
- The box checks each party's certificate against the root baked into its image, then the signature against the certified key. The box refuses keys from the local `make dev-keys` recipe in step 4 of the run.

## 1. Write the agreement

1. Open the UI and log in.
2. On the **Agreements** page, select **New agreement**.
3. Type a name. Use letters, digits, `-` and `_` only.
4. Type the question this audit answers.
5. Select **Create draft**. The UI opens the plan page.
6. Select **Edit this plan** to open the builder.
7. Edit the graph or the JSON text. The builder refuses a plan the box would refuse.
8. Select **Save** in the builder. The change log on the plan page records the version.

## 2. Sign the agreement

The lab signature is automatic when the server holds the lab key. The auditor signs by hand. Only the auditor signature freezes the agreement.

1. On the plan page, select **Sign this plan**.
2. Check that the lab card shows "signed this version automatically by the server".
3. As the auditor, select **Sign this version in this browser**.
4. Read the digest in the confirmation dialog. Select **Continue**.
5. Check that the auditor card shows "signed this version".

After a valid signature exists, the UI refuses to save the agreement. To change it, select **Edit this plan** on the plan page. This makes a new version and removes the existing signatures. Both parties sign the new version again.

### Set up signing

Do this once per browser, before the first signature. Nothing runs outside the browser.

1. On the Sign page, type a password twice and select **Set up signing**.
2. Keep the key file that downloads, and its password. They are the only way to sign as the auditor in another browser.
3. The first setup for the auditor replaces the key the box trusts. Confirm the dialog. The UI opens a pull request and a release, and every page waits until the box trusts the key, a few minutes.
4. The card shows "Ready to sign". Continue with "Sign the agreement".

In another browser, open **Already set up in another browser?**, choose the key file, type its password and select **Use this key file**. No release is needed.

A browser that signed before root keys shows **Continue with this key** when the box trusts its key. Select it once. The key file saved for that browser is the auditor key file from then on.

To sign with keys kept outside the browser, open **Other ways to sign** on the Sign page and follow the commands there.

## 3. Start a run

1. On the **Agreements** page, select **Run** next to the signed agreement. The **Runs** page opens.
2. Check that the **Signed by both parties** field shows the agreement.
3. Under **Recorded sessions**, select the sessions to audit. Filter by time and user if needed.
4. Select **Start the run**. The run page opens.

The run page shows six steps:

1. Verify the plan.
2. Attest the box.
3. Sign the test export.
4. Submit the logs.
5. Wait for the signed report.
6. Verify the report offline.

Each step shows **waiting**, **running** or **failed**. A finished step shows the time it finished. The page refreshes itself.

## 4. Read the result

The run page shows the outcome at the top.

- **Flagged** or **Not flagged**: the plan completed and step 6 verified the report. The report result shows below the outcome.
- **Completed**: the plan completed and the report has no flag field.
- **Execution failed — no verdict**: the box ran the plan and the plan failed. The page shows the error code and what to check.
- **Run stopped — no verified verdict**: a step failed before the report. Nothing after it ran.
- **Run interrupted — no verified verdict**: the server restarted during the run. Start a new run.

A verdict counts only when the line "Report verification: passed" shows. Without that line, treat the result as unverified.

The run page also names the plan version the run sent: the agreement, the short plan digest and whether both signatures verified when the run started. Select it to open that exact version read only, with its steps and the inputs of each step, so you can check whether a flag comes from the records or from how the blocks are connected. The Runs list shows the same link in the **Plan version** column.

The run folder holds the frozen agreement, the output of each step and the report. Its location is `AIAB_RUNS_DIR`, by default `ui/runs/`.
