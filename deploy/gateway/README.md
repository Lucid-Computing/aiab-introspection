# Optional verified host gateway

This gateway is active for the hosted demo as of 2026-09-19. Caddy retains its
Basic Auth boundary and routes the AIAB API to this gateway on loopback port 8444.
The previous hosted local-mode service remains available only as a rollback target.

The standalone `deploy/docker-compose.gateway.yml` publishes only
`127.0.0.1:8444`. The host sees plaintext browser traffic and holds the gateway
bearer token; it remains trusted. Caddy must continue to authenticate the complete
existing site before forwarding its API requests. The gateway does not authenticate
loopback callers, so local host processes are also trusted.

Before each accepted request the gateway runs the reviewed Tinfoil CLI, checks the
independently approved release digest, creates the SDK's attested and TLS-pinned
transport, fetches identity/manifest, and verifies the separately approved policy
digest. The policy must require gateway bearer authentication. Only then does it
send the inference body and gateway bearer token through that same pinned client.
User Authorization, Cookie, forwarding and other headers are never passed onward.
The SDK's automatic retry after a TLS key change is disabled: a changed identity
requires a fresh verification before a new request. Redirects and upstream error
bodies are suppressed; no access/SDK logs are emitted by the gateway entrypoint.

Preparation inputs:

- An actual enclave hostname, reviewed `owner/repo`, approved Tinfoil release digest
  (`tinfoil.hash`, not the OCI digest), and independently generated policy digest.
- A reviewed, checksum-verified static Tinfoil CLI v0.18.5 binary matching the host
  architecture. Set `TINFOIL_CLI_PATH` to its absolute host path; it is mounted
  read-only. The container intentionally does not download a CLI on startup.
- A separate 32–256 character URL-safe gateway token installed at
  `/etc/aiab-introspection/gateway.secret`, readable by container UID 65532 with
  narrowly scoped host permissions. Provision the same token as the enclave's
  `AIAB_GATEWAY_TOKEN` and set `AIAB_REQUIRE_GATEWAY_TOKEN=true`. Do not use either
  provider secret as this credential. Provider secrets are not mounted here.
- Export the four explicitly named public variables in the Compose file from
  independently reviewed records. The gateway does not obtain approvals from the
  running endpoint, and rejects all-zero placeholder digests.

Build with Docker BuildKit/Buildx (required for the Dockerfile-specific context
allowlist), and start only once those inputs exist:

```sh
docker buildx build --load -f deploy/Dockerfile.gateway -t aiab-verified-gateway:local .
docker compose --env-file /dev/null -f deploy/docker-compose.gateway.yml up -d
curl --fail http://127.0.0.1:8444/aiab/health
```

After independent acceptance, the existing Caddy API handler can be changed from
its current local appliance upstream to `127.0.0.1:8444` (plain HTTP over loopback).
Preserve the surrounding Basic Auth, route rewriting and every other site handler;
this preparation does not make that change. Verify audit artifacts and benign/block
requests through the same external URL after any authorized cutover. Roll back the
API upstream to the prior local service if acceptance fails, and label that mode
honestly as local/unattested.

This is a serial, buffered demo gateway: inference requests are limited to 1 MiB,
responses to 2 MiB, only GET `/v1/models` and POST `/v1/chat/completions` are supported,
and concurrent requests fail closed with 503. Audit GETs and `/aiab/health` also
verify identity and policy. Query strings and alternate encoded routes are rejected.
It is not a streaming or high-availability proxy. The entire CLI, SDK, audit and
inference operation runs in a disposable subprocess
with a 30 second deadline. Body receipt is limited to 10 seconds. Deadline or
cancellation kills the whole worker process group and allows up to two seconds
for reaping before releasing the admission slot. A background reaper remains if
the kernel delays termination; Compose init also reaps orphaned verifier children.
Request bodies and the token travel over anonymous
pipes, never command arguments, environment variables or temporary files. Worker
output is bounded and diagnostics are discarded.
Tests use simulated verification evidence; they establish no live hardware claim.

The token file must be owned by root or UID 65532 and have mode 0400 or 0600;
for the non-root container, use ownership UID 65532 and mode 0400. Symlinks in any
path component are rejected. Never broaden it to group/world-readable mode.

The current UI and Caddy allow 45 seconds per request; the gateway allocates at
most 10 seconds to body receipt and 30 seconds to the entire verified operation,
leaving margin for cleanup and network transit. Full fresh CLI plus SDK verification
can consume that budget and will fail closed with 503; no live latency has been
measured. Before cutover, measure successful and failed request latency through the
unchanged browser/Caddy path. The gateway never skips verification to meet deadlines.
