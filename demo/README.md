# Web chat demo

For the separate six-scenario adversarial walkthrough, see
[ADVERSARIAL.md](ADVERSARIAL.md); its default dry-run needs no deployment.

The page demonstrates the block decision and signed aggregate counts. It is an
operator-controlled, untrusted interface, outside the AIAB production image.
Use synthetic messages. The ML-training preset is “dangerous” only according to
the AI-capabilities classifier definition; it contains nothing hazardous.

## Local sequence: verify → benign → blocked → finalized rollup

1. Install Docker/Compose and `uv`. Follow [inference setup](../inference/README.md)
   to obtain a supported GGUF, or use an existing model. Export an absolute
   `INFERENCE_MODEL_PATH` and check `test -s "$INFERENCE_MODEL_PATH"`. No model is
   downloaded automatically; missing bind sources fail without creating files.
   Stop the monitor stack with `make local-down` first if it owns port 8443.
2. From the repository root, run `UV_PYTHON=3.12 make sync`, then `make demo-up`.
   The stack serves webchat on `http://localhost:8081` and AIAB on
   `https://localhost:8443`; both bind only to loopback. Inference is private to
   the Compose network. AIAB uses block mode and ten-second report periods.
   With no exported `TINFOIL_API_KEY`, it uses the keyword `MockClassifier`.
   To use the verified remote classifier, export your key securely and optionally
   `CLASSIFIER_MODEL` before starting; the default requested model is
   `gpt-oss-safeguard-120b`. Verification failures never fall back to the mock.
   Compose commands do not read `.env`. Do not print or share resolved Compose
   configuration containing a real exported key.
3. Run the auditor:

   ```sh
   uv run --locked auditor verify --host localhost --insecure-local
   ```

   This checks local manifest/identity consistency only. It does **not** verify
   enclave attestation, image identity, or the TLS identity; a local endpoint can
   replace both the signing key and reports. Do not describe this as an attested
   demo. The browser page is not an auditor.
4. Open `https://localhost:8443/aiab/health` directly and explicitly accept the
   temporary self-signed certificate for this local demo, if your browser allows
   it. Then open `http://localhost:8081` (use exactly `localhost`, not `127.0.0.1`,
   because the CORS origin is exact). The status should show the AIAB URL and
   mode `block`. If necessary, click **Reconnect** after accepting the certificate.
   Browsers that prohibit local certificate exceptions require locally trusted
   TLS setup; do not disable browser security globally. Rebooting AIAB rotates
   the local certificate and signing key, so trust may need refreshing.
5. Click **Load benign prompt**, then **Send**. A normal assistant reply appears.
   Click **Load ML-training prompt**, then **Send**. The red **Blocked by AIAB**
   card shows the returned message, classifier id, and confidence (0.95 for this
   preset with the mock; the target is at least 0.8). No inference answer appears
   for the blocked turn. The user message remains visible. Only successful turns
   enter later conversation requests; **New conversation** clears page memory.
   Nothing is stored in browser persistent storage, console logs, or analytics.
6. A request admitted at the start of a fresh period can take up to **40 seconds**
   to appear in a finalized report: the ten-second demo period plus the fixed
   thirty-second processing allowance. [Reporter finalization](../aiab/reporter.py)
   waits until period end plus `processing_timeout_seconds`, even when the
   request has already completed; that allowance is defined in
   [the runtime configuration](../aiab/config.py). Wait 45 seconds after the
   blocked response to include a small scheduling margin, then run:

   ```sh
   sleep 45
   uv run --locked auditor rollup --host localhost --insecure-local
   ```

   Look for at least `1 blocked` in the totals (`requests_blocked >= 1`) among finalized signed reports.
   A live partial period is not a finalized report. If the relevant period has
   not finalized, wait another ten seconds and repeat. Rollups and keys are held
   in memory and reset with an AIAB restart. Other requests can affect counts.
   These local signatures still have the identity limitations described above.

Stop with `make demo-down`; the model file is preserved. If readiness takes longer
than five minutes, use `docker compose --env-file /dev/null -f
deploy/docker-compose.demo.yml up --build -d --wait --wait-timeout 600`.

## Use an existing public Tinfoil AIAB

This section is conditional on an operator having deployed and independently
verified the AIAB; this repository's web demo does not deploy infrastructure or
establish attestation. Follow the reviewed [deployment instructions](../deploy/tinfoil/README.md)
and install the required attestation tooling before claiming verified identity.
Never use `--insecure-local` against a public endpoint or substitute an observed
digest for an independently approved expected digest.

Set that deployment's `CORS_ALLOW_ORIGIN` to the exact web page origin, such as
`http://localhost:8081`, before it starts. The allowed origin is frozen for the
boot and affects the manifest. Set `ENFORCEMENT_MODE=block`, approve the resulting
manifest, and use a trusted HTTPS hostname for the deployed AIAB. `AIAB_URL` must
be an HTTP(S) origin, optionally followed by an unencoded ASCII path prefix.
Path segments may contain letters, digits, `.`, `_`, `~`, and `-`; standalone
`.`/`..` segments, duplicate slashes, percent encodings, credentials, query strings,
fragments, backslashes, and control characters are rejected. The prefix is retained
when appending API paths; `config.js` and presets remain relative to the UI page.
For the non-attested same-origin host setup, see [the hosting guide](../deploy/HOSTING.md).

Build and run just the UI against that already-running deployment:

```sh
docker build -t aiab-webchat:local webchat
docker run --rm --name aiab-webchat-public --log-driver none \
  -p 127.0.0.1:8081:8081 \
  -e AIAB_URL=https://YOUR-VERIFIED-AIAB-HOST aiab-webchat:local
```

First verify using independently approved Tinfoil release and policy digests (the
release digest is not the OCI image digest):

```sh
uv run --locked auditor verify --host YOUR-VERIFIED-AIAB-HOST --repo OWNER/REPO \
  --expected-digest APPROVED_TINFOIL_RELEASE_DIGEST \
  --expected-policy-digest APPROVED_POLICY_DIGEST
```

Then send the benign prompt and ML-training prompt as above, wait for the deployed
report period to finalize, and obtain the verified rollup:

```sh
uv run --locked auditor rollup --host YOUR-VERIFIED-AIAB-HOST --repo OWNER/REPO \
  --expected-digest APPROVED_TINFOIL_RELEASE_DIGEST \
  --expected-policy-digest APPROVED_POLICY_DIGEST
```

If verification fails, stop at that limitation. UI success and a locally checked
signature do not complete the verified sequence.
If the UI is hosted publicly, serve it over HTTPS and set the AIAB's CORS origin
to that exact HTTPS origin. The browser fetches only its same-origin configuration
and presets plus the configured AIAB's health, model list, and chat API. It cannot
keep a secret API key; do not place one in `AIAB_URL` or frontend configuration.

The standalone nginx image is digest-pinned; its build context is `webchat/`.
The repository root `.dockerignore` intentionally keeps browser sources out of
the production AIAB build context. nginx access logging is disabled, and the
container does not proxy conversation requests or persist browser chat history.
