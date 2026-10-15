#!/usr/bin/env python3
"""Prepare and execute one exact, non-destructive seven-day container stop."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

CLI = "/home/jon/.local/bin/tinfoil"
RECORD = Path("/etc/aiab-introspection-stop/stop-target.json")
NAME = "aiab-introspection"
FIELDS = {"schema", "container_id", "name", "repo", "accepted_at", "stop_at"}


class StopError(ValueError):
    """Fixed diagnostics only; never include CLI responses or credential paths."""


def utc(value: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
        raise StopError("Expected an exact UTC timestamp")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        raise StopError("Invalid UTC timestamp") from None


def stamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_record(value: object) -> dict:
    if not isinstance(value, dict) or value.keys() != FIELDS:
        raise StopError("Invalid stop record")
    try:
        parsed_id = UUID(value["container_id"])
        valid_id = str(parsed_id) == value["container_id"] and parsed_id.int != 0
    except (ValueError, TypeError, AttributeError):
        valid_id = False
    if (
        not valid_id
        or value["schema"] != "aiab-seven-day-stop/v1"
        or value["name"] != NAME
        or not isinstance(value["repo"], str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", value["repo"])
    ):
        raise StopError("Invalid exact container identity")
    if utc(value["stop_at"]) != utc(value["accepted_at"]) + timedelta(days=7):
        raise StopError("Stop deadline must be exactly seven days after acceptance")
    return value


def parse(raw: bytes) -> object:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise StopError("Duplicate JSON key")
            result[key] = value
        return result

    try:
        return json.loads(raw, object_pairs_hook=unique)
    except (ValueError, UnicodeError):
        raise StopError("Invalid JSON response or record") from None


def command(operation: str, container_id: str) -> dict:
    # Pin both executable and config location; ignore ambient admin/API key or
    # endpoint overrides. The CLI alone loads its saved login, never this script.
    if operation not in {"get", "stop"}:
        raise StopError("Unsupported operation")
    try:
        result = subprocess.run(
            [CLI, "container", operation, container_id, "--output", "json"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=60,
            check=False,
            env={
                "HOME": "/home/jon",
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "TINFOIL_CONFIG": "/home/jon/.tinfoil/config.json",
                "TINFOIL_CONTROLPLANE_URL": "https://api.tinfoil.sh",
                "TINFOIL_NO_UPDATE_CHECK": "1",
            },
        )
    except (OSError, subprocess.TimeoutExpired):
        raise StopError("Container command unavailable or timed out") from None
    if result.returncode != 0 or len(result.stdout) > 1_048_576:
        raise StopError("Container command failed")
    value = parse(result.stdout)
    if not isinstance(value, dict):
        raise StopError("Invalid container response")
    return value


def identity(record: dict, remote: dict) -> str:
    if any(remote.get(key) != record[key] for key in ("name", "repo")):
        raise StopError("Container identity changed; refusing to stop")
    if remote.get("id") != record["container_id"]:
        raise StopError("Container UUID changed; refusing to stop")
    if not isinstance(remote.get("status"), str) or not remote["status"]:
        raise StopError("Missing container state")
    return remote["status"].lower()


def write_new(path: Path, value: str, mode: int) -> None:
    """Create one reviewed artifact with an exact mode, independent of umask."""
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        mode,
    )
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w") as stream:
            descriptor = -1
            stream.write(value)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def prepare(container_id: str, repo: str, accepted_at: str, output: Path, *, now: datetime) -> dict:
    accepted = utc(accepted_at)
    if not accepted <= now <= accepted + timedelta(hours=24):
        raise StopError("Preparation requires a successful acceptance within the last 24 hours")
    record = validate_record(
        {
            "schema": "aiab-seven-day-stop/v1",
            "container_id": container_id,
            "name": NAME,
            "repo": repo,
            "accepted_at": accepted_at,
            "stop_at": stamp(accepted + timedelta(days=7)),
        }
    )
    remote = command("get", container_id)
    if identity(record, remote) not in {"ready", "running"}:
        raise StopError("Accepted container must currently be running")
    if remote.get("debug") is not False or remote.get("disable_cc_mode") is not False:
        raise StopError("Accepted container must use production confidential compute")
    # A new directory prevents silently replacing an existing accepted schedule.
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    write_new(output / "stop-target.json", json.dumps(record, indent=2) + "\n", 0o600)
    write_new(output / "aiab-introspection-stop.service", service(), 0o644)
    write_new(output / "aiab-introspection-stop.timer", timer(record), 0o644)
    return record


def service() -> str:
    return """[Unit]
Description=Stop only the accepted AIAB enclave after seven days
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=0

[Service]
Type=oneshot
User=jon
Group=jon
ExecStart=/usr/bin/python3 /opt/aiab-introspection/stop_after_week.py execute
Restart=on-failure
RestartSec=15min
TimeoutStartSec=240
KillMode=control-group
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
UMask=0077
StandardOutput=journal
StandardError=null
"""


def timer(record: dict) -> str:
    due = utc(record["stop_at"]).strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"""[Unit]
Description=One-time stop of accepted AIAB enclave, seven days after acceptance

[Timer]
OnCalendar={due}
Persistent=true
OnBootSec=2min
AccuracySec=1s
RandomizedDelaySec=0
Unit=aiab-introspection-stop.service

[Install]
WantedBy=timers.target
"""


def read_record(path: Path, *, dry_run: bool) -> dict:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size > 4096
                or metadata.st_mode & 0o022
                or ((not dry_run or path == RECORD) and metadata.st_uid != 0)
            ):
                raise StopError("Stop record must be a protected root-owned regular file")
            return validate_record(parse(stream.read(4097)))
    except OSError:
        raise StopError("Stop record unavailable") from None


def execute(record: dict, *, now: datetime, dry_run: bool = False) -> str:
    validate_record(record)
    if now < utc(record["stop_at"]) and not dry_run:
        return "Not due; no container stopped"
    state = identity(record, command("get", record["container_id"]))
    if state == "stopped":
        return "Exact accepted container is already stopped"
    if dry_run:
        return "Dry run verified exact target; no container stopped"
    # Never resolve by name, enumerate org containers, delete, restart or relaunch.
    identity(record, command("stop", record["container_id"]))
    if identity(record, command("get", record["container_id"])) != "stopped":
        raise StopError("Stop not yet confirmed; retry required")
    return "Exact accepted container stopped and confirmed"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("prepare")
    generate.add_argument("--container-id", required=True)
    generate.add_argument("--repo", required=True)
    generate.add_argument(
        "--accepted-at", required=True, help="UTC time of successful live acceptance"
    )
    generate.add_argument("--acceptance-confirmed", action="store_true", required=True)
    generate.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("execute")
    run.add_argument("--record", type=Path, default=RECORD)
    run.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        now = datetime.now(UTC)
        if args.command == "prepare":
            result = prepare(args.container_id, args.repo, args.accepted_at, args.output, now=now)
            print(
                f"Prepared exact target {result['container_id']} for UTC stop {result['stop_at']}"
            )
            print("No timer installed or activated")
        else:
            print(
                execute(
                    read_record(args.record, dry_run=args.dry_run), now=now, dry_run=args.dry_run
                )
            )
    except (StopError, OSError):
        print("AIAB scheduled stop failed safely; operator inspection or retry required")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
