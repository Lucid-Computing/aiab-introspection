# Live, guided audit demonstration

On the configured development host, launch from any directory:

```sh
/home/jon/projects/aiab-audit-execution/scripts/demo-live
```

Or, from a checkout containing this script, run `./scripts/demo-live`.
It pauses for Enter at each stage in an interactive terminal. Each subprocess
command is printed before execution, shell-quoted for copying, with its working
directory. Credential file paths appear, but their contents are never printed. Use `--auto` for an
unattended run, or `--case benign` / `--case bio` for one example (default: both).

The walkthrough:

1. Shows the classification task, threshold and report schema; verifies both plan signatures.
2. Shows the actual SDK wrapper, connection setup and verification-check source,
   then verifies the live Tinfoil service and saves its report identity anchor.
3. Displays synthetic logger records and signs a stub export using the demo logger key.
4. Submits that export to the live `/aiab/runs` endpoint and displays the run ID.
5. Waits for actual attested inference, verifies and displays the signed report.
6. Verifies the saved report again in a process with networking disabled.

Only the logger and its records are stubbed. There is no mock inference fallback.
A negative benign case and positive bio case must match their expected results for
the script to declare success. A mismatch is reported, not hidden. These two cases
are a demonstration, not evidence of broad classifier accuracy.

The current plan is baked into the image: this script **does not submit or deploy
a new plan**. It executes the existing jointly signed plan against new submissions.
Historical fixture timestamps deliberately match its signed time window.

Requirements: `uv`, repository dependencies (installed automatically from the
lockfile), network access to Tinfoil/GitHub attestation services, and locally
provisioned `lab.token` and `logger.key` under
`~/.local/share/aiab-audit-demo/`. Override with `--key-directory PATH`.
Never put keys in the repository. A different host needs its own authorized copy
of these credentials; the script does not provision access.

Artifacts go to a new private `/tmp/aiab-live-demo-*` directory, printed at the end.
Use `--output /absolute/new-directory` for a persistent location. It includes the
plan snapshot, release pins, online identity anchor, submissions, admissions,
signed reports, offline verification outputs and summary. Offline verification
relies on the anchor verified online; it is not a standalone hardware endorsement.

The currently committed plan and deployment pins must match the running service.
If the service is unavailable or pins differ, the script stops with an error;
it never bypasses attestation. If a service restart changes the report identity
mid-demo, restart the demonstration rather than accepting a different signing key.
