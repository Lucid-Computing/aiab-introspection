# Run the live demo yourself

You need a macOS or Linux terminal (Windows: use WSL), Git access to this private
repository, and `uv` installed. No Tailscale, Docker, GPU, Anthropic key or Tinfoil
account is needed. This client talks to the already-running confidential service.

**Ask Jonathan for `aiab-demo-credentials.tar.gz`, shared privately.** It contains
only the demo lab API token and stub logger signing key—not plan-signing keys or
provider administration credentials. It grants demo submission/report access;
do not publish it or commit it. Both are shared demo credentials, not a dedicated
PM account. The walkthrough only submits the included synthetic fixtures.

## 1. Get the code

```sh
git clone https://github.com/Lucid-Computing/aiab-introspection.git
cd aiab-introspection
```

If you already have the repo, use `git switch master` and `git pull --ff-only` from
a clean checkout. If GitHub denies access, ask for repository access first.

If `uv --version` fails, install uv using its
[installation instructions](https://docs.astral.sh/uv/getting-started/installation/).
The demo installs locked Python dependencies automatically on first launch.

## 2. Install the privately shared demo credentials

Assuming the archive is in your Downloads folder:

```sh
mkdir -p "$HOME/.local/share/aiab-audit-demo"
chmod 700 "$HOME/.local/share/aiab-audit-demo"
tar -xzf "$HOME/Downloads/aiab-demo-credentials.tar.gz" -C "$HOME/.local/share/aiab-audit-demo"
chmod 600 "$HOME/.local/share/aiab-audit-demo/lab.token" "$HOME/.local/share/aiab-audit-demo/logger.key"
```

The archive contains exactly `lab.token` and `logger.key`. Keep it private, or
delete the downloaded archive once installed. If you already have demo credentials,
keep using those rather than overwriting them.

## 3. Run

From the repository root:

```sh
./scripts/demo-live
```

Press Enter to advance. Allow a few minutes, especially for dependency installation
and live inference. Commands, SDK verification source, synthetic input and signed
reports are displayed. Run `./scripts/demo-live --auto` to skip the pauses.

You should see:

- The existing plan's two signatures verify.
- Your local client verifies the remote box with the Tinfoil SDK.
- Benign example: `flagged: false`.
- Bio example: `flagged: true`.
- Both report signatures verify, including a separate offline verification step.
- `LIVE DEMO PASSED` and the directory containing your saved artifacts.

Only the logger is stubbed. The service, inference and reports are live. This is a
**run submission** demo using the already-deployed signed plan folder, not a new
plan deployment. New baked plans still require rebuilding/redeploying the image.

## If something fails

- **Missing credentials:** repeat step 2, or supply `--key-directory /absolute/path`.
- **Credential permissions:** run the `chmod 600` command above.
- **Authentication failure:** ask Jonathan for current demo credentials.
- **Connection/attestation/release mismatch:** check internet access and ask Jonathan
  whether the demo deployment or pins changed. Do not bypass verification.
- **Signed run failure or unexpected classification:** retain the artifact directory
  and share the synthetic report with Jonathan. The script deliberately fails rather
  than using fake inference. Two examples do not establish general accuracy.

See [the detailed walkthrough](LIVE_DEMO.md) for flags and trust boundaries.
