# Tinfoil integration evidence and open questions

Reviewed on 2026-09-17 against the official documentation and Python SDK 0.14.0.
Live API classification and container deployment remain unverified: implementation
and tests deliberately do not read the user's secret or make a keyed API call.

1. **TLS and app port.** The shim exposes configured paths and forwards to
   `shim.upstream-port` over its private `shim-net` bridge. Secure clients verify
   the enclave before sending requests. The implemented Tinfoil mode uses plain HTTP
   on port 8080 behind that shim; the TLS termination model
   is an inference from those routing and connection docs, not a tested deployment.
   The application port must match the configured upstream port; the docs do not
   mandate a universal port. [Networking and routing](https://docs.tinfoil.sh/containers/config-networking),
   [making requests](https://docs.tinfoil.sh/containers/connecting).

2. **Persistent signing keys.** Encrypted persistent volumes are available with
   CVM 0.14.4 or later. A client holds a volume secret and supplies it after
   attestation, manually or through its own keyserver. Private-secret release can
   be conditioned on an approved enclave release. This could preserve an
   application signing key across restarts, but rollback protection is explicitly
   absent and the keyserver becomes an additional trust component. AIAB v1 keeps
   its report key in memory and rotates it every boot.
   [Encrypted volumes](https://docs.tinfoil.sh/containers/encrypted-volumes),
   [private secrets](https://docs.tinfoil.sh/containers/private-secrets).

3. **Outbound connections.** Egress is closed by default. Declare an allowlist
   network and attach the application to it; every contacted hostname, including
   redirects, must appear explicitly and wildcards are unsupported. The reviewed
   SDK discovers routers through `atc.tinfoil.sh`, then reaches the selected
   router, `github-proxy.tinfoil.sh`, and verification services including
   `kds-proxy.tinfoil.sh` or `tdx-proxy.tinfoil.sh`. Sigstore trust metadata and
   redirects may require additional hosts. The governed inference hostname also
   needs authorization. A deployment must validate the complete list against its
   selected platform/router; allowing only `inference.tinfoil.sh` is insufficient.
   This inventory is source-derived and is not a verified deployment allowlist.
   The template therefore explicitly uses `egress: open`, which permits public
   destinations while Tinfoil blocks private/internal ranges. Frozen upstream URLs,
   disabled upstream redirects and the verified classifier SDK constrain application
   destinations; there is no externally enforced hostname allowlist claim. A complete
   allowlist must be established in a live run before narrowing this setting.
   [Networking rules](https://docs.tinfoil.sh/containers/config-networking),
   [SDK source](https://github.com/tinfoilsh/tinfoil-python/tree/main/src/tinfoil).

4. **Structured model.** `gpt-oss-safeguard-120b` is documented for custom-policy
   classification with structured output. AIAB defaults to that requested model
   when a key is configured and uses `response_format` with `json_schema`, a
   strict two-field schema, and temperature zero. JSON is still parsed strictly;
   malformed, non-finite, truncated, or refused output is an error. Cheapest
   acceptable model and policy accuracy remain unmeasured. A low temperature does
   not guarantee deterministic model behavior or calibrated confidence.
   [Safety models](https://docs.tinfoil.sh/models/safety),
   [structured outputs](https://docs.tinfoil.sh/guides/structured-outputs).

5. **Verification metadata.** `TinfoilAI.get_verification_document()` exposes
   `security_verified`, release tag/digest, code/enclave fingerprints, verifier
   identity, verification time, measurements and per-step status. AIAB requires
   successful verification before admission, logs `classifier_verified`, and
   exposes a bounded metadata allowlist at `/aiab/health`; raw step errors and
   complete documents are excluded. A later verification failure permanently
   closes this boot's admission gate. The manifest records the verification
   policy, not a promise to pin every future provider release.
   [Python SDK](https://docs.tinfoil.sh/sdk/python-sdk),
   [verification document source](https://github.com/tinfoilsh/tinfoil-python/blob/main/src/tinfoil/client.py).

6. **Latency.** Unknown until a live run from the intended deployment. Block mode
   adds a classifier round trip and potentially one retry; current bounds are a
   ten-second attempt and thirty-second request deadline. The runtime owns the
   single retry and the underlying SDK inference client's retries are disabled.
   SDK certificate rotation may separately reverify before transmitting data.

7. **Attestation claim.** The SDK verifies hardware evidence against published
   code/config measurements and binds its transport to the verified enclave.
   AIAB uses the default `tinfoilsh/confidential-model-router` verification path;
   the public document identifies that router. The requested safeguard model
   alias and its catalog configuration repository are not an independently pinned
   immutable weight digest. The router's downstream verification and provider
   releases remain part of the trust chain. This implementation makes no stronger
   per-classification weight-identity or policy-correctness claim.
   [Verification architecture](https://docs.tinfoil.sh/verification/verification-in-tinfoil),
   [SDK implementation](https://github.com/tinfoilsh/tinfoil-python/blob/main/src/tinfoil/__init__.py).

**SDK storage.** The SDK normally persists a cache-scoping secret. AIAB passes a
fresh opaque per-boot value explicitly, which stays in memory and is neither a user
identifier nor an API key. It is absent from reports and manifests. This scopes
prompt-cache sharing for the boot; it does not disable provider prompt caching or
provide per-user cache isolation. Public attestation/trust metadata may still be
cached by SDK dependencies. The image sets cache/data directories under `/tmp`, and
the Tinfoil template mounts `/tmp` as tmpfs with a read-only root; no persistent
volume is configured. [Prompt caching](https://docs.tinfoil.sh/sdk/prompt-caching).

8. **Auditor digest semantics.** Current CLI v0.18.5 JSON `digest` and Python SDK
   0.14.0 `release_digest` are the release's `tinfoil.hash`/Sigstore subject digest.
   They are not Docker image digests or raw SNP/TDX measurements. AIAB's
   `--expected-digest` pins that release value, and the tagged measured config must
   separately pin the approved image. The CLI JSON has no explicit debug boolean;
   the upstream verifier rejects debug hardware policies and the adapter requires
   status `ok`, complete measurements and matching TLS keys. Unknown record fields
   fail closed. All fetched audit artifacts use the SDK's verified transport and
   are checked against the same release, code measurement and TLS key before/after
   each request. These paths are tested offline; real deployment acceptance remains
   unperformed. [CLI source](https://github.com/tinfoilsh/tinfoil-cli/blob/v0.18.5/attestation.go),
   [deployment and approval steps](../deploy/tinfoil/README.md).

9. **Release/runtime acceptance.** CVM 0.14.7 and 2 CPUs/8192 MB follow the current
   official template. Actual organization/repository, public inference origin,
   registry digest, measured release, hostname and independently approved policy
   remain unavailable. A temporary checksum-verified Buildx 0.37.1 setup and pinned
   BuildKit 0.26.2 produced matching clean timestamp-normalized OCI image digests;
   see [REPRODUCIBILITY.md](REPRODUCIBILITY.md). Tinfoil startup verification failure
   now aborts the application lifespan, and the template has a loopback readiness
   probe. No public image or deployment was created.

10. **Adversarial deployment acceptance.** The staged
    [adversarial driver](../demo/ADVERSARIAL.md) exercises local startup refusal and
    provides operator-assisted live probes. Tamper/debug/configuration/restart
    deployments and direct-inference bypass remain unperformed. A threshold change
    in measured YAML can change the Tinfoil release digest while keeping the same
    OCI image. To isolate policy rejection, independently approve the candidate
    release while retaining the original policy pin. The current adapter suppresses
    CLI error details, so a generic verifier rejection cannot by itself establish
    debug-policy causality; retain independently reviewed hardware-debug evidence.
