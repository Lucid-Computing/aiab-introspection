# Stop the accepted enclave after seven days

This helper prepares a **stop**, never a deletion, of the one new
`aiab-introspection` container. Existing organization containers are not listed or
mutated. No schedule exists until the installation step below is explicitly run.
The installed [Tinfoil CLI v0.18.5 stop implementation](https://github.com/tinfoilsh/tinfoil-cli/blob/v0.18.5/container.go)
resolves a UUID directly and calls the container stop endpoint. It preserves the
container record and secret bindings; no delete command exists in this helper.

First complete successful live attestation, independently pinned release/policy,
gateway/browser benign and blocked-request acceptance. Record that actual UTC
acceptance timestamp to the second. Build/setup time is not part of the seven-day
window. The helper requires that acceptance was within the last 24 hours and that
the supplied UUID currently identifies a running production confidential-compute
container named exactly `aiab-introspection` in the approved repository. The flag
`--acceptance-confirmed` records the operator's assertion; the helper itself does
not replace attestation or browser acceptance.

Prepare into a new directory outside the repository (substitute the three public
values; the command makes only a read-only `container get` API request):

```sh
python3 deploy/tinfoil/stop_after_week.py prepare \
  --container-id ACCEPTED_IMMUTABLE_UUID \
  --repo APPROVED_OWNER/REPO \
  --accepted-at YYYY-MM-DDTHH:MM:SSZ \
  --acceptance-confirmed \
  --output /tmp/aiab-seven-day-stop
python3 deploy/tinfoil/stop_after_week.py execute \
  --record /tmp/aiab-seven-day-stop/stop-target.json --dry-run
```

Inspect the public `stop-target.json` and generated unit files. The stop timestamp
must be exactly seven days after acceptance. Dry-run validates the live identity,
even before the deadline, and never issues stop. It does not change either the
acceptance time or the deadline. A new output directory is required, so preparation
cannot silently overwrite an existing accepted schedule.

After reviewing that exact target and time, install the reviewed helper and record
as root-owned files. The public record uses a separate directory; never change the
permissions of `/etc/aiab-introspection`, which contains protected secrets. The system service runs as `jon` and lets
`/home/jon/.local/bin/tinfoil` load its existing saved login from the normal home
configuration. The helper pins that configuration path and the official
`https://api.tinfoil.sh` control-plane endpoint, and disables the CLI update check
so it does not attempt to write under the read-only home sandbox. No API key is copied into repository files, unit files, command
arguments or environment variables. Do not print or copy the saved CLI config.

```sh
sudo install -d -o root -g root -m 0755 /opt/aiab-introspection /etc/aiab-introspection-stop
sudo install -o root -g root -m 0755 deploy/tinfoil/stop_after_week.py \
  /opt/aiab-introspection/stop_after_week.py
sudo install -o root -g root -m 0644 /tmp/aiab-seven-day-stop/stop-target.json \
  /etc/aiab-introspection-stop/stop-target.json
sudo install -o root -g root -m 0644 /tmp/aiab-seven-day-stop/aiab-introspection-stop.service \
  /etc/systemd/system/aiab-introspection-stop.service
sudo install -o root -g root -m 0644 /tmp/aiab-seven-day-stop/aiab-introspection-stop.timer \
  /etc/systemd/system/aiab-introspection-stop.timer
sudo systemd-analyze verify /etc/systemd/system/aiab-introspection-stop.service \
  /etc/systemd/system/aiab-introspection-stop.timer
sudo systemd-run --wait --pipe --collect \
  --unit=aiab-stop-dry-run --property=User=jon --property=Group=jon \
  --property=ProtectSystem=strict --property=ProtectHome=read-only \
  --property=PrivateTmp=true --property=NoNewPrivileges=true \
  /usr/bin/python3 /opt/aiab-introspection/stop_after_week.py execute --dry-run
sudo systemctl daemon-reload
sudo systemctl enable --now aiab-introspection-stop.timer
systemctl list-timers --all aiab-introspection-stop.timer
```

The transient dry-run above verifies record access and the saved CLI login as
`jon` under the same filesystem restrictions before timer activation. It never
stops the container. If it fails, do not activate the timer. Do not start the real
service manually to test it; use `execute --dry-run`. Inspect
`systemctl show aiab-introspection-stop.timer -p NextElapseUSecRealtime` to confirm
the exact UTC deadline is installed. These commands install/activate a schedule and
are intentionally not called by `prepare`. Before replacing any previously installed
schedule, inspect its target and deadline and resolve that existing schedule first.

At expiry the service gets the exact UUID, verifies UUID/name/repo, issues stop by
UUID, then gets that UUID again to confirm `stopped`. An already stopped container
is a successful no-op. A replacement with the same name but a different UUID is
never followed. A renamed or differently configured target fails closed for
operator inspection. The helper never invokes start, restart, relaunch or delete.
A stop that is still pending, an API error or invalid response exits with failure;
`Restart=on-failure`, `RestartSec=15min` and disabled start-rate limiting retry it.
`Persistent=true` fires a missed deadline after the host restarts. `OnBootSec=2min`
also performs a due-check on every boot: this recovers a stop attempt that fired
and failed before a reboot, whose service restart state would otherwise be lost.
Before the deadline this boot check returns successfully without any API call.
After the deadline it resumes the same immutable-target stop and retry behavior. CLI operations
have 60-second deadlines; systemd also caps each attempt at 240 seconds and kills
its entire process group. CLI output and errors stay captured and are not logged;
the journal contains only fixed helper success/failure messages.

A systemd timer can survive this host's reboot, but cannot execute while the host is
offline indefinitely or the saved Tinfoil login is revoked. Check timer/service
status and the container state after the deadline. If the org credentials change,
renew the saved login securely; do not put credentials into the units. This is a
cost-control stop schedule, not a promise of uninterrupted infrastructure uptime.
