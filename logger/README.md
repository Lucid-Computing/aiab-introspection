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
logging proxy ──proxy-data (read-only)──► this server ──metadata──────► agreement UI ──► the box
 (plain logs)                             (reads, seals)  sealed records    (signs, forwards)   (opens)
                                                │
                                                └── attests the box, fetches its key (Tinfoil SDK)
```

It reads the proxy's log files in place, from the proxy's data volume mounted read-only, the
way the worm-outbox does: no key and no HTTP between the two. The proxy keeps its log as one
stream of JSON lines in three kinds of files (its `segments.py`): sealed
`segments/requests-<first>-<last>.jsonl.zst`, pending `segments/requests-<first>-<last>.jsonl`,
then the live `requests.jsonl`. Each sealed or pending file is parsed once; the live file is
read on from where the last read stopped, up to its last complete line. A file that cannot be
read (gone, corrupt, truncated) is skipped and named in `/v1/status`; the rest is still served.

## The API

Every route but `/healthz` takes this server's key in the `x-log-key` header.

| Route | What it returns |
|---|---|
| `GET /v1/sessions` | `{"whos": [...], "sessions": [...]}`, newest session first. `?who=alice` keeps one person's. |
| `POST /v1/export` | Body `{"sessions": ["<id>", ...]}`. The records of those sessions, sealed. |
| `GET /v1/status` | The last read of the proxy's files (`at`, `error`, `result`), the files it could not read (`problems`), the number of calls held, the box sealed to. |
| `POST /v1/sync` | Reads the proxy's files again now (it also happens every `SYNC_SECONDS`). Returns `{"new", "segments", "live", "calls"}`: files parsed this time, sealed and pending files listed, lines in the live file, calls held. |
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

Every route answers from the last read; none reads a file. Until the first read after a
start has finished, `/v1/sessions` is empty and `/v1/status` says `"at": null`.

Errors: `401` without the key, `404` for a session id it does not hold, `422` for a body
with any other field or sessions that hold no text, `502` when the box does not answer or
its attestation fails, `503` from `/v1/sync` when the proxy's directory cannot be read.

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
- **This server reads plaintext**, from the proxy's files, and holds the parts a run needs in
  memory. It keeps no copy on disk and writes nothing to the proxy's volume. It must run where
  the proxy runs.

## Running it

The image holds no secret; everything comes from the environment.

| Variable | Meaning |
|---|---|
| `PROXY_DIR` | The proxy's data directory (the one holding `requests.jsonl` and `segments/`), mounted read-only. Default `/proxy-data`. |
| `API_KEY` | The key callers of this server send as `x-log-key`. Pick a long random one. |
| `BOX_HOST` | The box to seal to, e.g. `aiab-dev.lucid-computing.containers.tinfoil.dev`. |
| `BOX_REPO` | Its Tinfoil config repo, e.g. `Lucid-Computing/aiab-tinfoil-staging-config`. |
| `SYNC_SECONDS` | How often to read the proxy's files again. Default `300`. |

A missing `API_KEY`, `BOX_HOST` or `BOX_REPO` stops the server at start. The image runs as
uid 10001, which must be able to read the proxy's files.

```bash
docker run -d -p 8080:8080 --read-only \
  -v proxy-data:/proxy-data:ro \
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
uv run --locked python -m pytest        # a proxy directory in the real layout and a stand-in box
uv run --locked ruff check . && uv run --locked ruff format --check .
PROXY_DIR=/path/to/logging-proxy/logs API_KEY=… BOX_HOST=… BOX_REPO=… \
  uv run --locked uvicorn --factory logserver.app:main --port 8080
```

The reading rules for a recorded call (`logserver/calls.py`) are the agreement UI's
(`ui/server/proxylogs.py`). Keep them the same until the UI reads from this server only.
