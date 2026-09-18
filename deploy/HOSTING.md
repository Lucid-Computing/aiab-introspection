# Host the non-attested local demo

Target: **https://jonathan.quest/aiab-introspection/** on server **206.189.165.5**.
This is the existing local-mode AIAB with the keyword mock classifier and local
llama.cpp inference. It has **no enclave attestation**. Public HTTPS authenticates
the website, not an enclave or the report-signing identity. Use synthetic messages.

The hosted Compose file fixes `AIAB_MODE=local`, block enforcement, and
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
nameservers, wildcard records, mail records, or the `files` Caddy site. This apex
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
automatically read. `INFERENCE_MODEL_PATH` is the only interpolated hosted setting.
For a standalone Compose executable use, for example,
`make COMPOSE=/absolute/path/to/docker-compose hosted-up` and the same override for
`hosted-config` and `hosted-down`. Stop `make demo-down` or `make local-down` first
if that stack owns ports 8081/8443. Keep Docker enabled on boot. A restart policy
restarts exited containers; it does not automatically repair an unhealthy service.

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
current Caddyfile with that example. Preserve `files.jonathan.quest` exactly. Keep
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
