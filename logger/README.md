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

Every route but `/healthz` takes the caller's key in the `x-log-key` header. Each box has
its own key, and the key decides which box an export is sealed to.

| Route | What it returns |
|---|---|
| `GET /v1/sessions` | `{"whos": [...], "sessions": [...]}`, newest session first. `?who=alice` keeps one person's. |
| `POST /v1/export` | Body `{"sessions": ["<id>", ...]}`. The records of those sessions, sealed. Optional `"offset": n` starts the record numbers at `n`, for a run that joins exports from several log servers. |
| `GET /v1/status` | The last read of the proxy's files (`at`, `error`, `result`), the files it could not read (`problems`), the number of calls held, the box this key seals to. |
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
  "content_bytes": 5120,
  "calls": [
    {"id": "…", "ts": "2026-10-28T08:08:41Z", "who": "tester", "status": 200,
     "duration_s": 1.84, "model": "claude-fable-5-1", "stop_reason": "end_turn",
     "request_bytes": 314, "messages": 1, "records": 2, "kinds": ["request", "response"],
     "content_bytes": 412}
  ]
}
```

`content_bytes` is how much text a run would send, a size and never the text. A record's
text is sent whole, however long.

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

Every route answers from the last read, and only an export reads a file: the text of the
calls it sends. Until the first read after a
start has finished, `/v1/sessions` is empty and `/v1/status` says `"at": null`.

Errors: `401` without the key, `404` for a session id it does not hold, `422` for a body
with any other field or sessions that hold no text, `502` when the box does not answer or
its attestation fails, `503` from `/v1/sync` when a source cannot be read, and from
`/v1/export` when a chosen call's text is no longer where the last read found it (the
proxy's log started again, say): sync, then ask again.

## What it guarantees, and what it does not

- **The caller cannot choose the key.** The boxes are this server's own setting, and each
  has its own API key. A caller gets the box its key belongs to and no other, so a leaked
  dev key cannot get logs sealed for the production box. A body that names a key or another
  box is refused. Otherwise a caller could have the logs sealed to a key of its own.
- **The key is the attested box's.** The Tinfoil SDK checks the chip's report against the
  config repo and pins the connection; `GET /aiab/pubkey` over it gives the key. The box
  makes a new key at every start, so the key is asked for at every export. Nothing sealed
  is stored.
- **The export is not signed here.** The agreement UI signs the sealed records, as it does
  today, with the logger key the box trusts. Sealing hides the text; it does not prove where
  the records came from. Moving that key to this server is a later step and changes no format.
- **This server reads plaintext**, from the proxy's files. It holds none of the text in
  memory: the index keeps each call's metadata and where its line is, and an export reads
  the chosen calls' text from the files again. It keeps no copy of the proxy's log on disk
  and writes nothing to the proxy's volume, so it must run where the proxy runs. Of the RSI
  plant it does keep a copy, the hub's files, under `DATA_DIR`.

## Running it

The image holds no secret; everything comes from the environment.

| Variable | Meaning |
|---|---|
| `PROXY_DIR` | The proxy's data directory (the one holding `requests.jsonl` and `segments/`), mounted read-only. Default `/proxy-data`. With the plant's hub set, the proxy is read only if this is set. |
| `API_KEY` | The key the caller sends as `x-log-key`. Pick a long random one. |
| `BOX_HOST` | The box to seal to, e.g. `aiab-dev.lucid-computing.containers.tinfoil.dev`. |
| `BOX_REPO` | Its Tinfoil config repo, e.g. `Lucid-Computing/aiab-tinfoil-staging-config`. |
| `BOXES` | Only for more than one box: their names, e.g. `dev,prod`. See below. |
| `SYNC_SECONDS` | How often to read the proxy's files again. Default `300`. |

| `SOURCES` | Only to keep the box to some sources, e.g. `proxy`. With `BOXES`, `<NAME>_SOURCES`. See below. |
| `PLANT_HUB_URL` | Only for the RSI plant: the address of the plant hub's read API. See below. |
| `PLANT_HUB_KEY` | Only for the RSI plant: a reader's token for that hub. |
| `DATA_DIR` | Only for the RSI plant: where the hub's files are kept between starts. Default `/data`. |

A missing `API_KEY`, `BOX_HOST` or `BOX_REPO` stops the server at start.

**The RSI plant as a source.** Set `PLANT_HUB_URL` and `PLANT_HUB_KEY` together and this
server also reads the plant's hub (`logserver/plant.py`). With `PROXY_DIR` set as well it
reads both, and the UI sees the proxy's sessions and the plant's in one list. Without
`PROXY_DIR` it reads the plant alone. The API is the same. A run gives its agents'
transcripts as sessions, and three more of its own: `<run>-jobs` (its Slurm jobs),
`<run>-network` (every connection on the lab's network) and `<run>-scores` (the models the
plant scored), sent by `plant-jobs`, `plant-network` and `plant-scores`. A plant session's
metadata also names its `run`. The hub's files are copied under `DATA_DIR`, which needs a
volume uid 10001 can write: a restart then fetches only what is new. A run whose records hold
something shaped like a provider key is held back whole and named in `/v1/status`; so is a
run the hub could not send. `/v1/sync` answers `503` when a source cannot be read, after
reading the others.

**Which box sees which source.** A box sees every source this server reads, unless
`<NAME>_SOURCES` (or `SOURCES`, for the one box) names fewer, e.g. `PROD_SOURCES=proxy`. A
caller neither lists nor exports a session of a source its box is not given.

```bash
docker run -d -p 8080:8080 --read-only --tmpfs /tmp \
  -v proxy-data:/proxy-data:ro -e PROXY_DIR=/proxy-data \
  -v logger-data:/data \
  -e PLANT_HUB_URL=https://… -e PLANT_HUB_KEY=… \
  -e API_KEY=… \
  -e BOX_HOST=aiab-dev.lucid-computing.containers.tinfoil.dev \
  -e BOX_REPO=Lucid-Computing/aiab-tinfoil-staging-config \
  ghcr.io/lucid-computing/aiab-logger:dev
```

**More than one box.** Set `BOXES` to a list of names and give each name its own three
variables, the name in capitals as the prefix. `API_KEY`, `BOX_HOST` and `BOX_REPO` are then
not used, and setting `API_KEY` as well stops the server.

```
BOXES=dev,prod
DEV_API_KEY=…    DEV_BOX_HOST=aiab-dev.lucid-computing.containers.tinfoil.dev    DEV_BOX_REPO=Lucid-Computing/aiab-tinfoil-staging-config
PROD_API_KEY=…   PROD_BOX_HOST=…                                                 PROD_BOX_REPO=…
```

The dev UI is given `DEV_API_KEY` and the production UI `PROD_API_KEY`. Two boxes may not
share a key. All callers see the same sessions; only the box an export is sealed to differs. The image runs as
uid 10001, which must be able to read the proxy's files.

The container can run with a read-only root if it has a tmpfs at `/tmp`. Checking the box's
attestation writes sigstore's trust root and the Tinfoil SDK's cache there (the image points
`XDG_CACHE_HOME` and `XDG_DATA_HOME` at it). Without it, `/v1/export` answers `502`.

```bash
docker run -d -p 8080:8080 --read-only --tmpfs /tmp \
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
