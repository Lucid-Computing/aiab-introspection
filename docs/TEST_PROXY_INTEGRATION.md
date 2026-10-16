# Test proxy receiver and audit adapter

The receiver implements the October 16 test proxy PUT contract. It accepts real
compressed segments, preserves their original bytes and acknowledges durable
storage. It does **not** claim the currently unsigned segments are authenticated
logger exports, and does not synthesize signatures to bypass audit admission.

## Installed receiver

- Host: `claude-dev.tail0f71c2.ts.net`, last observed tailnet IP `100.118.42.114`.
- Service: `aiab-proxy-ingest.service`, enabled at boot, running as `jon`.
- Local address: `http://127.0.0.1:8090`.
- Storage: `/home/jon/.local/share/aiab-proxy-ingest/llm-proxy-test.lucidcompute.com/`.
- Source: `/home/jon/projects/aiab-audit-execution/static/aiab/proxy_ingest.py`.
- Service template: `static/deploy/proxy-ingest/aiab-proxy-ingest.service`.

This host holds plaintext conversations outside the Tinfoil audit enclave. The
receiver does not log request bodies. Files are private (0600, directories 0700).
Do not commit downloaded logs or normalized imports. For production confidentiality,
receive/decrypt in the attested boundary or agree on this trusted storage hop.

At implementation time Tailscale initially reported Running without tag:worm.
Requesting that tag using `tailscale up` transitioned to NeedsLogin. The session subsequently returned to Running with no tags. HTTPS forwarding is
configured, but certificate provisioning and sender delivery are **not yet
verified**. Have the tailnet admin approve/configure the receiver tag and ACLs.
The installed forwarding configuration is:

```sh
sudo tailscale serve --bg --https=443 http://127.0.0.1:8090
curl https://claude-dev.tail0f71c2.ts.net/healthz
```

Ask Alex to configure the test proxy destination using the confirmed IP/DNS name.
There is currently only one sender destination; coordinate before switching it.
ACLs must restrict uploads to the proxy sender. Tailscale HTTPS alone does not
prove the producer's measured code or authenticate records cryptographically.

The receiver serves GET /healthz and PUT
/v1/llm-proxy-test.lucidcompute.com/<segment>. The 201/200 responses include the
SHA-256 and byte count of the exact uploaded compressed bytes. Same-name different
bytes return 409. Atomic exclusive file creation prevents concurrent overwrites;
files and the containing directory are fsynced before success. Interrupted uploads
are not acknowledged. Uploads are capped at 16 MiB. zstd validation happens during
decoding, not transport receipt. A receipt proves storage only.

## Decode an actual received segment

Install system `zstd` and `uv sync --locked` from static/. Supply a private JSON
mapping of **actual request IDs** to confirmed agent/session identities:

```json
{"request-id-from-log": {"agent_id": "test", "session_id": "confirmed-session"}}
```

The current source uses a shared `who: test`; that is not an individual agent ID.
Until the logger documents session metadata, the adapter requires this mapping
rather than guessing or merging everyone's sessions. One line becomes two records
(request and response), preserving raw JSON/SSE and tool data verbatim as content.
Non-UTF-8 bodies fail explicitly rather than silently discarding bytes. Source
stream, segment start and within-segment offsets preserve stable ordering.

```sh
cd /home/jon/projects/aiab-audit-execution/static
uv run python -m aiab.proxy_ingest decode \
  /absolute/path/llmproxy-test-LOGID-FIRST-LAST.jsonl.zst \
  --mapping /absolute/private/request-identities.json \
  --output /absolute/private/import.json
```

Output is `aiab-unsigned-proxy-import/v1`, with `verified: false`, original segment
hash/range, and records matching AIAB's record schema. No original file is changed.
Output creation refuses overwrite. Decompressed data is capped at 64 MiB. The
record range must match line count (inclusive first/last); confirm this convention
against a real fixture before treating the integration as end-to-end tested.

## Boundary still to integrate

This import is deliberately **not** accepted by POST /aiab/runs, which requires
signed evidence. No deployed runtime checks have been weakened. To complete native
integration, obtain a real fixture, identity metadata, logger verification library,
trusted signing key and chain/checkpoint protocol. Verify original bytes before
normalization inside the audit trust boundary; bind the normalization version and
source proof to the input commitment. Persist sequence/checkpoint state to detect
replays, gaps and rollback; filenames alone cannot prove complete time coverage.

The signed plan must also authorize the real identities/date window and a batching
policy compatible with its 100 KB input limit (segments are around 1 MB). Do not
silently truncate or treat independently classified chunks as equivalent to an
agreed multi-log audit. A temporary unsigned diagnostic mode would require an
explicit signed-plan/runtime policy and report provenance field, not mock signing.

## Verification

`uv run pytest tests/test_proxy_ingest.py -q` covers durable concurrent duplicates,
conflicts, exact receipt metadata, source/path/size rejection, real zstd decoding,
SSE/base64 preservation, missing identity mappings and inconsistent ranges.
These tests use synthetic fixtures; actual proxy delivery remains an external check.
