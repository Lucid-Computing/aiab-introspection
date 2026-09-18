# Host the non-attested local demo

Target: **https://jonathan.quest/aiab-introspection/** on server **206.189.165.5**.
This is the existing local-mode AIAB with the keyword mock classifier and local
llama.cpp inference by default, with optional verified Tinfoil classification and
Sonnet answer inference below. The self-hosted AIAB has
**no enclave attestation**. Public HTTPS authenticates
the website, not an enclave or the report-signing identity. Use synthetic messages.

The base hosted Compose file fixes `AIAB_MODE=local`, block enforcement, and
`mock-keywords/v1`; it does not inherit a Tinfoil key or classifier from the shell.
It publishes only `127.0.0.1:8081` (webchat) and `127.0.0.1:8443` (AIAB local TLS).
Inference has no host port. All services restart with Docker unless explicitly
stopped. AIAB's report state, signing keys, and local certificate are ephemeral
and reset when AIAB restarts. Browser conversation history stays in the open
page's memory across server restarts; reload the page or select **New conversation**
to clear it. Caddy is the only public entry point.

## DNS change

In the DNS provider's editor for `jonathan.quest`, first record the current apex
records for rollback. The 2026-09-18 preflight found Porkbun nameservers and apex
A records `207.207.210.107` and `207.207.210.229` (parking), with no apex AAAA.
In Porkbun, edit the **A** record with name **@** (some editors display
`jonathan.quest`) so its only value is **206.189.165.5**, TTL **300 seconds**.
Do not add a URL or `/aiab-introspection` to DNS. Remove any other apex A values
that point elsewhere. If an apex AAAA record exists but this server is not serving
the site over that IPv6 address, remove that stale apex AAAA record too.

**Preserve `files.jonathan.quest` and all its records unchanged.** Do not change
nameservers, wildcard records, or mail records. Preserve the `files` service and
apply its sensitive-file guard below independently of apex deployment. This apex
change moves every `jonathan.quest` path to this server: migrate any existing apex
site handlers before switching DNS. The app fragment itself only handles its own
path prefix and leaves other apex paths to the existing configuration.

Check propagation from more than one resolver:

```sh
dig +short A jonathan.quest @1.1.1.1
dig +short A jonathan.quest @8.8.8.8
dig +short AAAA jonathan.quest @1.1.1.1
dig +short A files.jonathan.quest @1.1.1.1
```

## Start the loopback services

Use Docker Engine, Docker Compose with `extends` and `--wait` support, and the
existing system Caddy (validated with stock Caddy 2.11.2). Obtain the model via
[the pinned model download](../inference/README.md#obtain-a-model). No service
downloads weights. From the repository root:

```sh
export INFERENCE_MODEL_PATH=/home/jon/projects/aiab-introspection/models/qwen2.5-1.5b-instruct-q4_k_m.gguf
test -s "$INFERENCE_MODEL_PATH"
make hosted-config
make hosted-up
curl --fail --silent --show-error http://127.0.0.1:8081/config.js
curl --fail --silent --show-error --insecure https://127.0.0.1:8443/aiab/health
```

Both Make targets pass `--env-file /dev/null`; the repository `.env` is never
automatically read. `INFERENCE_MODEL_PATH` is the only interpolated base-stack
setting; the optional overlays add explicit host secret file paths.
For a standalone Compose executable use, for example,
`make COMPOSE=/absolute/path/to/docker-compose hosted-up` and the same override for
`hosted-config` and `hosted-down`. Stop `make demo-down` or `make local-down` first
if that stack owns ports 8081/8443. Keep Docker enabled on boot. A restart policy
restarts exited containers; it does not automatically repair an unhealthy service.

## Optional Sonnet answer inference

The separate `docker-compose.hosted.anthropic.yml` overlay uses Anthropic's native
Messages API and exposes the same text-only `/v1/chat/completions` interface to the
browser. Its pinned default is [`claude-sonnet-4-6`](https://platform.claude.com/docs/en/models/sonnet-4-6/overview),
an active Sonnet model. Block policy stays in place; the keyword classifier is
used unless the Tinfoil overlay below is also selected. Anthropic receives only
allowed conversations. This provider does not attest AIAB.

The API key stays in a private host file mounted read-only into AIAB as a Docker
secret. It is read once at startup, retained in the AIAB process, and sent only in
the HTTPS request to `api.anthropic.com`. It is absent from browser assets, browser
requests, model/health/report endpoints, container environment, image, and resolved
Compose configuration. Host root and the AIAB process can access it. Do not paste
the key into chat, `.env`, command arguments, Docker build arguments, or Git.

Provision a new private file from an interactive server terminal. This prompt hides
input; the command text contains no credential. UID 65532 matches the image user;
the enclosing host directory is root-only. The command refuses to overwrite an
existing file. If using Docker user-namespace remapping, use the corresponding
mapped host UID instead.

```sh
sudo install -d -m 0700 /etc/aiab-introspection
sudo python3 - <<'PY'
import getpass
import os

key = getpass.getpass("Anthropic API key (hidden): ")
if not key or len(key) > 4096 or any(not 33 <= ord(c) <= 126 for c in key):
    raise SystemExit("Invalid key format")
fd = os.open("/etc/aiab-introspection/anthropic.secret", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
with os.fdopen(fd, "w", encoding="ascii") as output:
    os.fchown(output.fileno(), 65532, 65532)
    output.write(key + "\n")
PY
```

The loader validates the in-container `/run/secrets/anthropic_api_key` mount. It
rejects symlinks in any component of that path, non-files, relative paths,
group/world-readable files, invalid owners, empty/oversized files, and whitespace
inside the key. The operator must ensure the host source path is absolute and has
no symlink components: after Docker resolves and mounts it, the app cannot inspect
the original host path. The loader accepts one optional final LF. Missing,
malformed, or unreadable secret files stop startup;
there is no fallback to local inference and no support for an `ANTHROPIC_API_KEY`
environment variable. Startup does not authenticate with Anthropic. A well-formed
but invalid or revoked key is discovered on completion and returns a sanitized
upstream failure.

With Docker Compose supporting `!reset` (v2.24.4 or newer), select the overlay
explicitly. The exported value is only an **absolute file path**, never the key.
Use the same two Compose files for subsequent operations:

```sh
export AIAB_ANTHROPIC_KEY_FILE=/etc/aiab-introspection/anthropic.secret
case "$AIAB_ANTHROPIC_KEY_FILE" in /*) ;; *) exit 1 ;; esac
docker compose --env-file /dev/null -f deploy/docker-compose.hosted.yml \
  -f deploy/docker-compose.hosted.anthropic.yml config --quiet
docker compose --env-file /dev/null -f deploy/docker-compose.hosted.yml \
  -f deploy/docker-compose.hosted.anthropic.yml up --build -d --wait --wait-timeout 300
```

The overlay disables the local inference dependency and places that service behind
the `local-inference` profile, so no model file is required. An already-running
local inference container may be stopped separately. The default `make hosted-up`
continues to select local inference and does not require a secret.

To rotate an existing Anthropic credential, run this command from the repository
root in an **interactive server terminal**, and paste the new key only at its
hidden prompt:

```sh
sudo python3 deploy/rotate_anthropic_secret.py
```

The helper never reads `.env` or the existing credential contents, and accepts no
credential arguments or environment variables. It fails if hidden terminal input
is unavailable. It requires 1–4096 printable ASCII non-space characters. The
existing `/etc/aiab-introspection` directory must be root:root with mode `0700`;
the existing `anthropic.secret` must be a regular, single-link file owned by
`65532:65532` with mode `0400`. Symlink components and unsafe parent permissions
are rejected. This helper assumes the standard container UID mapping described
above and does not support remapped host UIDs.

Rotation writes and syncs a randomly named staging file inside the root:root
`0700` directory `/etc/aiab-introspection/.anthropic-rotation`, then atomically
replaces the destination with the completed `65532:65532/0400` file. Readers see
either the complete old file or the complete new file. Linux cannot atomically
replace an existing filename directly from an unnamed `O_TMPFILE` inode, so
rotation needs this protected staging name. Handled failures clean up the staged
file. Abrupt termination or power loss can leave a staging file accessible only
through the root-only directory; the next invocation removes stale entries only
after strict filename, type, ownership, permissions, size, and link-count checks.
Unexpected entries stop rotation. Directory locks prevent concurrent helper runs,
and file and directory syncs provide durability. A sync failure after replacement
can report failure even though the new credential is installed.

After successful rotation, **recreate the AIAB container**; a restart retains the
old bind-mounted inode and does not load the replaced file. For the Sonnet-only
configuration above:

```sh
export AIAB_ANTHROPIC_KEY_FILE=/etc/aiab-introspection/anthropic.secret
docker compose --env-file /dev/null -f deploy/docker-compose.hosted.yml \
  -f deploy/docker-compose.hosted.anthropic.yml \
  up -d --no-deps --force-recreate --wait --wait-timeout 300 aiab
```

If using verified Tinfoil classification, keep `AIAB_TINFOIL_KEY_FILE` set to its
existing host path and also include `-f deploy/docker-compose.hosted.tinfoil.yml`
after the Anthropic overlay. Preserve every other overlay and setting used by the
running deployment. The rotation helper itself does not recreate containers.

Only the configured model is listed and accepted. Supported inputs are leading
system messages, then text user/assistant messages starting and ending with user;
nonempty OpenAI text-part arrays are accepted. Streaming, tools, images, developer
messages, assistant prefills, unknown fields, and unsupported generation options
are rejected before an Anthropic request. `n` must be 1; the output defaults to 512
tokens and is capped at 2048; either `max_tokens` or `max_completion_tokens` may set
the cap. Either `temperature` or `top_p` may be set in [0, 1]; at most four nonempty
stop strings of at most 256 characters are allowed. Other generation controls are
unsupported. The upstream has a 15-second timeout within the 30-second processing
deadline and a 1 MiB response cap, with no redirects, environment proxies, or
automatic retries. Long answers can time out. Provider errors and headers are
replaced by fixed public error messages.

Keeping the credential private does **not** limit public API spending: this demo
has no login, per-user rate quota, or application billing cap. Anyone who can reach
the public chat can consume this account's Anthropic usage. The concurrency and
output limits bound individual requests, not total account usage. Configure an
appropriate provider-side spending limit before enabling a paid public endpoint.

## Optional verified Tinfoil classification

Layer `docker-compose.hosted.tinfoil.yml` after the hosted base and Sonnet overlay
to classify every admitted conversation with `gpt-oss-safeguard-120b` through the
reviewed Tinfoil SDK. AIAB stays in **local mode with block enforcement**. The SDK
verifies the classifier's serving infrastructure and pins its TLS connection;
this does **not** enclave-attest the self-hosted AIAB, Caddy, browser, signing key,
or reports. Host root and the AIAB process can access both keys and conversation
contents. Caddy terminates public HTTPS and can access conversation contents too.
The requested safeguard model is a provider alias, not an independently pinned
weight digest.

Use the private file provisioning procedure above with a separate Tinfoil file:

If the repository `.env` already contains `TINFOIL_API_KEY`, install it without
displaying the value or exporting it into the shell:

```sh
sudo python3 deploy/install_tinfoil_secret.py
```

The helper reads `.env` relative to its repository, accepts one literal unquoted,
single-quoted, or double-quoted assignment (optionally prefixed with `export`),
and does no shell expansion or escape processing. It requires 1–4096 printable
ASCII non-space bytes. It creates or restricts the root-owned destination
directory to `0700`, then atomically installs `tinfoil.secret` owned by
`65532:65532` with mode `0400`. It refuses any existing destination, including
symlinks. Linux `O_TMPFILE` support is required: the temporary inode has no name
and disappears on failure or interruption. It does not start or recreate any
containers. An existing key must be handled separately as an intentional rotation;
the helper never overwrites it.

Alternatively, enter the key at a hidden prompt:

```sh
sudo install -d -m 0700 /etc/aiab-introspection
sudo python3 - <<'PY'
import getpass
import os

key = getpass.getpass("Tinfoil API key (hidden): ")
if not key or len(key) > 4096 or any(not 33 <= ord(c) <= 126 for c in key):
    raise SystemExit("Invalid key format")
fd = os.open("/etc/aiab-introspection/tinfoil.secret", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
with os.fdopen(fd, "w", encoding="ascii") as output:
    os.fchown(output.fileno(), 65532, 65532)
    output.write(key + "\n")
PY
```

As an operator prerequisite, the host source path must be absolute and have no
symlink components. Its file must be regular, readable by the container UID, owned
by root or that UID, and exactly mode `0400` or `0600`. The app validates the
in-container `/run/secrets/tinfoil_api_key` mount and each component of that path;
it cannot validate the original host source path after Docker resolves it.
Compose's file-backed secrets preserve host permissions, so provisioning
the correct host owner/mode is required; do not make the key world-readable to fix
a mount error. User-namespace remapping requires the mapped host UID. The loader
reads the file once and freezes the key for the boot. Both `TINFOIL_API_KEY_FILE`
and `TINFOIL_API_KEY` being present is an error, even when the latter is empty.
The base stack omits credential variables; the overlay injects only
`TINFOIL_API_KEY_FILE=/run/secrets/tinfoil_api_key`. It mounts the Tinfoil key only
in AIAB and preserves the separate Anthropic secret, existing loopback ports,
disabled container logging, dropped capabilities, and read-only root filesystem.
The only writable filesystem addition is a 256 MiB `/tmp` tmpfs with
`nosuid,nodev,noexec` for the SDK's public verification caches. The image already
directs XDG cache/data paths there. It contains no persistent transcript volume;
prompt-cache isolation uses a separate random in-memory secret for each boot.

Select exactly these three reviewed files for configuration review and subsequent
operations:

```sh
export AIAB_ANTHROPIC_KEY_FILE=/etc/aiab-introspection/anthropic.secret
export AIAB_TINFOIL_KEY_FILE=/etc/aiab-introspection/tinfoil.secret
case "$AIAB_ANTHROPIC_KEY_FILE" in /*) ;; *) exit 1 ;; esac
case "$AIAB_TINFOIL_KEY_FILE" in /*) ;; *) exit 1 ;; esac
docker compose --env-file /dev/null -f deploy/docker-compose.hosted.yml \
  -f deploy/docker-compose.hosted.anthropic.yml \
  -f deploy/docker-compose.hosted.tinfoil.yml config --quiet
docker compose --env-file /dev/null -f deploy/docker-compose.hosted.yml \
  -f deploy/docker-compose.hosted.anthropic.yml \
  -f deploy/docker-compose.hosted.tinfoil.yml up --build -d --wait --wait-timeout 300
```

These commands export **file paths only**. Keys never enter Compose interpolation,
container environment, browser assets or requests, images, public health/model
endpoints, reports, manifests, or logs. The key is passed to the Tinfoil SDK inside
AIAB. The overlay also works with only the hosted base when local answer inference
is desired; in that case a local model file is still required.

These guarantees apply to those two documented file combinations. This overlay
does not sanitize arbitrary additional Compose files: an intervening overlay that
sets `TINFOIL_API_KEY` can leave that value in the container environment and
resolved Compose output. AIAB rejects the resulting ambiguous credential sources
before loading a key file or admitting traffic, but startup rejection cannot undo
that environment exposure. Do not add a legacy credential variable in another
overlay.

Missing, unreadable, unsafe, empty, or malformed key files abort configuration.
A production classifier model without a key, or a key combined with the mock
model, also fails startup. SDK verification must succeed before health becomes
ready or chat traffic is admitted. In local mode a verification failure leaves
only an unavailable diagnostic endpoint: health and chat return 503, and Compose
`--wait` fails. There is no mock fallback. A well-formed revoked credential may be
discovered only on classification; credential checks do not send a startup prompt.
Classification failures return 503 without forwarding. A verification failure
during operation closes admission for the rest of that boot; restart and successful
verification are required for recovery.

Every admitted text conversation is sent to the Tinfoil classifier. A policy block
returns 403; only policy-allowed messages reach Anthropic. Answers are not classified
in block mode. Both classifier requests (including blocked chats) and allowed answer
requests can incur provider costs. The public demo has no per-user quota or billing
cap. To rotate either key, securely replace its private host file and recreate AIAB
with the same three Compose files. Returning to the base mock configuration is an
explicit operator policy change, never an automatic recovery step.

This configuration and failure behavior are tested with synthetic credentials and
offline SDK/HTTP mocks. These tests do not establish live credential validity,
provider accuracy, latency, or successful hosted deployment.

## Protect the existing home-directory file service

The existing `files.jonathan.quest` service serves `/home/jon`, which includes
repository worktrees. Git ignore rules do not prevent HTTP access. Keep production
credentials only in `/etc/aiab-introspection`, outside that document root, with the
owner and permissions documented above. After verifying the deployed credential's
metadata, remove any untracked, never-committed repository copy of `tinfoil.secret`
without opening it. Check Git tracking and all reachable history by path name
first; if a credential was committed, stop for a separate rotation/history plan.
Never display or fetch a credential to check whether it is exposed.

Install [files-sensitive-paths.caddy](caddy/files-sensitive-paths.caddy) as
`/etc/caddy/files-sensitive-paths.caddy`. In the existing **files** site, put its
import first in an explicit `route`, then nest the existing authentication, root,
and file-server directives in a fallback `handle`. Preserve their values and all
other site settings, including TLS and logging:

```caddyfile
files.jonathan.quest {
    route {
        import /etc/caddy/files-sensitive-paths.caddy
        handle {
            # Existing basic_auth block, root, and file_server directives here.
        }
    }
    # Existing non-handler site settings remain here.
}
```

The guard returns 404 for `.env` and any basename ending in `.secret` anywhere
under the file-service root, including nested projects and URL-encoded paths.
It runs before authentication and file serving; normal paths retain the existing
authentication behavior. The explicit ordering follows Caddy's
[route directive](https://caddyserver.com/docs/caddyfile/directives/route), and the
[path matcher](https://caddyserver.com/docs/caddyfile/matchers#path_regexp) checks
the decoded URL path. This is independent of the AIAB public routes below.

Prepare a candidate beside the active Caddyfile, preserve the active file's owner
and permissions, and run `caddy adapt` and `caddy validate` on the candidate before
atomically replacing the active file and reloading Caddy. Capture command output
privately: adapted configuration may contain authentication hashes. Validate that
the guard precedes the unchanged authentication/root/file-server handlers. Check
with HEAD requests and status output only, never fetch credential contents:

```sh
curl --silent --show-error --head --output /dev/null --write-out '%{http_code}\n' \
  https://files.jonathan.quest/projects/aiab-introspection/.env
curl --silent --show-error --head --output /dev/null --write-out '%{http_code}\n' \
  https://files.jonathan.quest/projects/aiab-introspection/tinfoil.secret
curl --silent --show-error --head --output /dev/null --write-out '%{http_code}\n' \
  https://files.jonathan.quest/
```

Expect 404 for protected paths and the existing unauthenticated 401 for `/`.

## Add the Caddy routes

Back up the live file, install the app fragment, and edit the existing Caddyfile:

```sh
AIAB_CADDY_BACKUP="/etc/caddy/Caddyfile.before-aiab.$(date -u +%Y%m%dT%H%M%SZ)"
sudo cp -a /etc/caddy/Caddyfile "$AIAB_CADDY_BACKUP"
printf 'Rollback file: %s\n' "$AIAB_CADDY_BACKUP"
sudo install -m 0644 deploy/caddy/aiab-introspection.caddy /etc/caddy/aiab-introspection.caddy
sudoedit /etc/caddy/Caddyfile
```

Inside the **existing `jonathan.quest` site block**, add:

```caddyfile
import /etc/caddy/aiab-introspection.caddy
```

If no apex site exists, append the site block from
[Caddyfile.example](caddy/Caddyfile.example) to the current file. Never replace the
current Caddyfile with that example. Preserve `files.jonathan.quest`, including its
sensitive-file guard. Keep
existing unrelated apex routes and place any unconditional fallback
`respond`/`rewrite`/`reverse_proxy` in a fallback `handle { ... }` block so it cannot
preempt the app's specific handlers. Do not enable request-body/access logging for
the demo or expose Docker ports beyond loopback.

```sh
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
```

After DNS reaches this server, Caddy obtains and renews the public certificate via
its existing ACME setup. Public TCP ports 80 and 443 must reach Caddy. There is no
reason to expose 8081, 8443, or inference. Browser TLS must verify normally.
`tls_insecure_skip_verify` is scoped only to Caddy's `https://127.0.0.1:8443` hop
because AIAB generates a fresh self-signed certificate on each local-mode boot.

The fragment redirects `/aiab-introspection` to the trailing-slash URL so relative
`config.js` and `demo_prompts.json` resolve inside the app. It strips
`/aiab-introspection` for the UI, and exactly `/aiab-introspection/api` for AIAB.
Thus public `/aiab-introspection/api/aiab/health` reaches `/aiab/health`. UI files
are allowlisted and unknown app paths return 404. Same-origin API requests need no
CORS policy.

The API has a 1 MB request-body cap and a four-request global concurrency cap;
overload receives 503 without proxy retries. This uses stock Caddy's
[`unhealthy_request_count`](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#passive-health-checks)
and [`request_body`](https://caddyserver.com/docs/caddyfile/directives/request_body).
It is overload protection, not a per-IP request-rate quota. Stock Caddy has no
rate-limit handler here; no unverified plugin syntax is installed. The public
demo has no login or per-user quotas, so anyone can submit synthetic prompts.

## Verify the public route

```sh
curl --silent --show-error --head https://jonathan.quest/aiab-introspection
curl --fail --silent --show-error https://jonathan.quest/aiab-introspection/config.js
curl --fail --silent --show-error https://jonathan.quest/aiab-introspection/api/aiab/health
curl --fail --silent --show-error https://jonathan.quest/aiab-introspection/api/v1/models
```

Expect a 308 to `/aiab-introspection/`, the configured API URL including its path,
and health showing local/block mode. Open the page, send the benign preset, then
the ML-training preset; the latter should return a mock policy block. Confirm
`files.jonathan.quest` and the prior apex paths still behave as before. Do not use
`--insecure` for public HTTPS checks. Before DNS cutover, `curl --resolve
jonathan.quest:443:206.189.165.5 ...` can test routing once Caddy has a trusted
certificate; it cannot bypass ACME validation requirements.

## Roll back

Restore the recorded Caddy backup from the same shell (or substitute its saved
absolute path), validate, and reload before stopping the application:

```sh
sudo cp -a "$AIAB_CADDY_BACKUP" /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
make hosted-down
```

This leaves the model file intact. Restore the recorded apex A/AAAA records at
the DNS provider if reversing the DNS cutover; leave `files` unchanged. DNS caches
may retain the new address until its TTL expires. If other Caddy changes happened
after deployment, remove only the app import/site addition instead of overwriting
those later changes with the old backup.
