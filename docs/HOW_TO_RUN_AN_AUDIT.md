# How to run an audit from the UI

This procedure uses the agreement UI against the live box. For the command-line route, see [BAKED_PLAN_OPERATIONS.md](BAKED_PLAN_OPERATIONS.md).

## Before you start

- You have a login. The lab account and the auditor account are different people.
- The UI server holds the lab signing key in `AIAB_LAB_KEY` and its certificate in `AIAB_LAB_CERTIFICATE`. The server then signs each version as the lab.
- The box trusts the auditor root. The auditor holds the root's private key on their own machine. If the box does not trust it, do the steps under "Replace the auditor root" first.
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

### Make and certify an auditor signing key

Do this once per browser, before the first signature. No release is needed.

1. On the Sign page, select **Make a key in this browser**.
2. Save the backup file and its password. The key stays in this browser only.
3. Copy the signing key shown in the card. On your own machine, run the `certify` command the card shows, with your root key file. It prints one JSON line, the certificate.
4. Paste the certificate under **Certificate** and select **Register the certificate**. The card shows "Certified by the auditor root production trusts". Continue with "Sign the agreement".

To sign with a key you keep outside the browser, open **Other ways to sign** on the Sign page. Run the commands it shows. Paste the signature and the certificate under **Paste a signature made elsewhere** and select **Verify and store**.

### Replace the auditor root

Only when the root is lost or exposed, or to revoke every signing key it certified. Make a root on your own machine (`python3 sign_digest.py keygen --out ~/auditor-root.key`; `sign_digest.py` downloads from the Sign page). Keep it off the server and out of the browser.

1. On the Sign page, open **Replace the auditor root key**.
2. Paste the root's public key and select **Submit this root key**. The UI opens a pull request that changes the trust file.
3. Wait. The Sign page shows the pull request, the check and the release.
4. When the release completes, the box trusts the root. Certify your signing key again with the new root.

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
