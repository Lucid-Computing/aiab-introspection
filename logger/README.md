# The logger's side of AIAB

A small web server that runs next to the logging proxy, on the proxy's machine. It is the
only part of AIAB that reads what was said. It gives the agreement UI two things:

- **Metadata**: the recorded sessions and their calls: who, when, model, status, sizes, how
  many records. Never the text.
- **Export**: the records of the sessions chosen for a run, each one's content sealed to the
  box's current key. Only the box can open them.

So the agreement UI (on Render) picks the input from metadata and forwards ciphertext. The
readable logs stay on the proxy's machine (MAC-377).

```
logging proxy ──/_logs──► this server ──metadata──────► agreement UI ──► the box
 (plain logs)             (reads, seals)  sealed records    (signs, forwards)   (opens)
                                │
                                └── attests the box, fetches its key (Tinfoil SDK)
```

## The API

Every route but `/healthz` takes this server's key in the `x-log-key` header.

| Route | What it returns |
|---|---|
| `GET /v1/sessions` | `{"whos": [...], "sessions": [...]}`, newest session first. `?who=alice` keeps one person's. |
| `POST /v1/export` | Body `{"sessions": ["<id>", ...]}`. The records of those sessions, sealed. |
| `GET /v1/status` | The last sync with the proxy, the number of calls held, the box sealed to. |
| `POST /v1/sync` | Asks the proxy for new segments now (it also happens every `SYNC_SECONDS`). |
| `GET /healthz` | `{"ok": true}`. No key. |

A session in `/v1/sessions`:

```json
{
  "id": "210eddd3-06cd-4efd-adb7-93d45fbf5674",
  "who": "tester",
  "started": "2026-10-28T08:08:41Z",
  "ended": "2026-10-28T08:12:35Z",
  "model": "claude-fable-5-1",
  "records": 17,
  "calls": [
    {"id": "…", "ts": "2026-10-28T08:08:41Z", "who": "tester", "status": 200,
     "duration_s": 1.84, "model": "claude-fable-5-1", "stop_reason": "end_turn",
     "request_bytes": 314, "messages": 1, "records": 2, "kinds": ["request", "response"]}
  ]
}
```

An export:

```json
{
  "scheme": "hpke-x25519-hkdf-sha256-aes256gcm/v1",
  "box": "aiab-dev.lucid-computing.containers.tinfoil.dev",
  "boot_id": "1eedf31e9bd5486391e4a16eef79c4da",
  "sessions": ["210eddd3-06cd-4efd-adb7-93d45fbf5674"],
  "records": [
    {"stream": "proxy", "batch": 0, "offset": 0, "agent_id": "tester",
     "session_id": "210eddd3-…", "ts": "2026-10-28T08:08:41Z", "kind": "request",
     "boot_id": "1eedf31e…", "enc": "<base64>", "content": "<base64 ciphertext>"}
  ]
}
```

Errors: `401` without the key, `404` for a session id it does not hold, `422` for a body
with any other field or sessions that hold no text, `502` when the proxy or the box does
not answer or the box's attestation fails.

## What it guarantees, and what it does not

- **The caller cannot choose the key.** The box is this server's own setting (`BOX_HOST`,
  `BOX_REPO`). A body that names a key or another box is refused. Otherwise a caller could
  have the logs sealed to a key of its own.
- **The key is the attested box's.** The Tinfoil SDK checks the chip's report against the
  config repo and pins the connection; `GET /aiab/pubkey` over it gives the key. The box
  makes a new key at every start, so the key is asked for at every export. Nothing sealed
  is stored.
- **The export is not signed here.** The agreement UI signs the sealed records, as it does
  today, with the logger key the box trusts. Sealing hides the text; it does not prove where
  the records came from. Moving that key to this server is a later step and changes no format.
- **This server holds plaintext**, on its disk (`DATA_DIR`) and in memory, like the proxy
  beside it. It must run where the proxy runs.

## Running it

The image holds no secret; everything comes from the environment.

| Variable | Meaning |
|---|---|
| `PROXY_URL` | The logging proxy's address, the one that serves `/_logs`. |
| `PROXY_KEY` | The proxy's reader key. |
| `PROXY_KEY_HEADER` | The header the proxy takes it in. Default `x-log-key`; the test proxy takes `x-proxy-key`. |
| `API_KEY` | The key callers of this server send as `x-log-key`. Pick a long random one. |
| `BOX_HOST` | The box to seal to, e.g. `aiab-dev.lucid-computing.containers.tinfoil.dev`. |
| `BOX_REPO` | Its Tinfoil config repo, e.g. `Lucid-Computing/aiab-tinfoil-staging-config`. |
| `DATA_DIR` | Where the proxy's segments are kept. Default `/data`. Optional volume. |
| `SYNC_SECONDS` | How often to ask the proxy for new segments. Default `300`. |

A missing `PROXY_URL`, `PROXY_KEY`, `API_KEY`, `BOX_HOST` or `BOX_REPO` stops the server at
start.

```bash
docker run -d -p 8080:8080 \
  -e PROXY_URL=https://llm-proxy.example -e PROXY_KEY=… \
  -e API_KEY=… \
  -e BOX_HOST=aiab-dev.lucid-computing.containers.tinfoil.dev \
  -e BOX_REPO=Lucid-Computing/aiab-tinfoil-staging-config \
  ghcr.io/lucid-computing/aiab-logger:dev
```

The image is built by `.github/workflows/build-logger-image.yml` on every push to `dev`
that touches this folder or the box's sealing code, and tagged with the commit and `dev`.

## Developing

```bash
cd logger
uv run --locked python -m pytest        # a stand-in proxy and a stand-in box, nothing mocked between
uv run --locked ruff check . && uv run --locked ruff format --check .
PROXY_URL=… PROXY_KEY=… API_KEY=… BOX_HOST=… BOX_REPO=… DATA_DIR=/tmp/logger-data \
  uv run --locked uvicorn --factory logserver.app:main --port 8080
```

The reading rules for a recorded call (`logserver/calls.py`) are the agreement UI's
(`ui/server/proxylogs.py`). Keep them the same until the UI reads from this server only.
