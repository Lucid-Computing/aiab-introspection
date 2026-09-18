"""Scheduled expiry must stop only the accepted UUID, never the rest of an org."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from deploy.tinfoil import stop_after_week as stop

ID = "c6316712-0728-4e0e-bad6-783709cc02b1"
OTHER = "9590b2bd-fcc4-4e48-b158-18889f795038"
ACCEPTED = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
DUE = ACCEPTED + timedelta(days=7)


def record():
    return {
        "schema": "aiab-seven-day-stop/v1",
        "container_id": ID,
        "name": "aiab-introspection",
        "repo": "owner/repo",
        "accepted_at": stop.stamp(ACCEPTED),
        "stop_at": stop.stamp(DUE),
    }


def remote(**changes):
    return {
        "id": ID,
        "name": "aiab-introspection",
        "repo": "owner/repo",
        "status": "Ready",
        "debug": False,
        "disable_cc_mode": False,
        **changes,
    }


def test_prepare_writes_exact_utc_timer_and_no_mutation(monkeypatch, tmp_path):
    calls = []

    def command(operation, target):
        calls.append((operation, target))
        return remote()

    monkeypatch.setattr(stop, "command", command)
    out = tmp_path / "prepared"
    value = stop.prepare(ID, "owner/repo", stop.stamp(ACCEPTED), out, now=ACCEPTED)
    assert calls == [("get", ID)] and value == record()
    assert json.loads((out / "stop-target.json").read_text()) == record()
    timer = (out / "aiab-introspection-stop.timer").read_text()
    assert "OnCalendar=2026-09-25 12:00:00 UTC" in timer and "Persistent=true" in timer
    assert "RandomizedDelaySec=0" in timer
    assert "OnBootSec=2min" in timer
    assert str(stop.RECORD) == "/etc/aiab-introspection-stop/stop-target.json"
    service = (out / "aiab-introspection-stop.service").read_text()
    assert "Restart=on-failure" in service and "RestartSec=15min" in service
    assert "StartLimitIntervalSec=0" in service and "KillMode=control-group" in service
    assert "User=jon" in service and "ProtectHome=read-only" in service
    assert "TimeoutStartSec=240" in service
    with pytest.raises(FileExistsError):
        stop.prepare(ID, "owner/repo", stop.stamp(ACCEPTED), out, now=ACCEPTED)


@pytest.mark.parametrize(
    "bad", ["--help", "aiab-introspection", ID.upper(), "00000000-0000-0000-0000-000000000000"]
)
def test_only_canonical_nonzero_uuid_allowed_before_network(bad, monkeypatch, tmp_path):
    monkeypatch.setattr(stop, "command", lambda *_: pytest.fail("Must reject before network"))
    with pytest.raises(stop.StopError):
        stop.prepare(bad, "owner/repo", stop.stamp(ACCEPTED), tmp_path / "p", now=ACCEPTED)


@pytest.mark.parametrize(
    "change",
    [
        {"id": OTHER},
        {"name": "unrelated"},
        {"repo": "other/repo"},
        {"status": "stopped"},
        {"debug": True},
        {"disable_cc_mode": True},
        {"debug": None},
    ],
)
def test_preparation_requires_exact_running_confidential_target(change, monkeypatch, tmp_path):
    monkeypatch.setattr(stop, "command", lambda *_: remote(**change))
    with pytest.raises(stop.StopError):
        stop.prepare(ID, "owner/repo", stop.stamp(ACCEPTED), tmp_path / "p", now=ACCEPTED)
    assert not (tmp_path / "p").exists()


@pytest.mark.parametrize("now", [ACCEPTED - timedelta(seconds=1), ACCEPTED + timedelta(days=2)])
def test_acceptance_time_is_recent_and_not_future(now, monkeypatch, tmp_path):
    monkeypatch.setattr(stop, "command", lambda *_: pytest.fail("Must reject before network"))
    with pytest.raises(stop.StopError):
        stop.prepare(ID, "owner/repo", stop.stamp(ACCEPTED), tmp_path / "p", now=now)


def test_before_due_never_contacts_or_stops(monkeypatch):
    monkeypatch.setattr(stop, "command", lambda *_: pytest.fail("Early timer must not call CLI"))
    assert stop.execute(record(), now=DUE - timedelta(seconds=1)) == "Not due; no container stopped"


@pytest.mark.parametrize("now", [ACCEPTED, DUE, DUE + timedelta(days=1)])
def test_dry_run_checks_identity_without_stop(now, monkeypatch):
    calls = []

    def command(operation, target):
        calls.append((operation, target))
        return remote()

    monkeypatch.setattr(stop, "command", command)
    assert "Dry run" in stop.execute(record(), now=now, dry_run=True)
    assert calls == [("get", ID)]


def test_stop_and_confirmation_use_exact_immutable_id(monkeypatch):
    calls = []
    replies = [remote(), remote(status="Stopping"), remote(status="Stopped")]

    def command(operation, target):
        calls.append((operation, target))
        return replies.pop(0)

    monkeypatch.setattr(stop, "command", command)
    assert "stopped and confirmed" in stop.execute(record(), now=DUE)
    assert calls == [("get", ID), ("stop", ID), ("get", ID)]


def test_stopped_target_idempotent_after_reboot(monkeypatch):
    calls = []

    def command(operation, target):
        calls.append((operation, target))
        return remote(status="Stopped")

    monkeypatch.setattr(stop, "command", command)
    assert "already stopped" in stop.execute(record(), now=DUE + timedelta(days=1))
    assert calls == [("get", ID)]


@pytest.mark.parametrize("changed", [{"id": OTHER}, {"name": "unrelated"}, {"repo": "other/repo"}])
def test_replacement_or_other_org_target_never_stopped(changed, monkeypatch):
    calls = []

    def command(operation, target):
        calls.append((operation, target))
        return remote(**changed)

    monkeypatch.setattr(stop, "command", command)
    with pytest.raises(stop.StopError):
        stop.execute(record(), now=DUE)
    assert calls == [("get", ID)]


def test_transient_failure_returns_retryable_failure_then_succeeds(monkeypatch):
    attempt = 0

    def command(operation, target):
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            raise stop.StopError("Transient command failure")
        return remote(status="Stopped")

    monkeypatch.setattr(stop, "command", command)
    with pytest.raises(stop.StopError):
        stop.execute(record(), now=DUE)
    assert "already stopped" in stop.execute(record(), now=DUE + timedelta(minutes=15))


def test_stop_not_confirmed_remains_retryable(monkeypatch):
    monkeypatch.setattr(stop, "command", lambda *_: remote(status="Stopping"))
    with pytest.raises(stop.StopError, match="retry required"):
        stop.execute(record(), now=DUE)


def test_cli_saved_login_no_secrets_argv_env_or_diagnostics(monkeypatch):
    def run(argv, **kwargs):
        assert argv == [stop.CLI, "container", "stop", ID, "--output", "json"]
        assert kwargs["env"] == {
            "HOME": "/home/jon",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "TINFOIL_CONFIG": "/home/jon/.tinfoil/config.json",
            "TINFOIL_CONTROLPLANE_URL": "https://api.tinfoil.sh",
            "TINFOIL_NO_UPDATE_CHECK": "1",
        }
        assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["timeout"] == 60
        return SimpleNamespace(
            returncode=1, stdout=b"private-cli-response", stderr=b"private-token"
        )

    monkeypatch.setattr(stop.subprocess, "run", run)
    with pytest.raises(stop.StopError) as error:
        stop.command("stop", ID)
    assert "private" not in str(error.value)
    with pytest.raises(stop.StopError):
        stop.command("delete", ID)


def test_record_corruption_and_writable_or_symlink_records_rejected(tmp_path):
    value = record()
    value["stop_at"] = stop.stamp(DUE - timedelta(seconds=1))
    with pytest.raises(stop.StopError):
        stop.validate_record(value)
    with pytest.raises(stop.StopError):
        stop.parse(b'{"id":"one","id":"two"}')
    path = tmp_path / "record.json"
    path.write_text(json.dumps(record()))
    path.chmod(0o666)
    with pytest.raises(stop.StopError):
        stop.read_record(path, dry_run=True)
    path.chmod(0o600)
    assert stop.read_record(path, dry_run=True) == record()
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(stop.StopError):
        stop.read_record(link, dry_run=True)


def test_boot_check_recovers_a_previously_fired_failed_stop(monkeypatch):
    calls = []
    failed = True

    def command(operation, target):
        nonlocal failed
        calls.append((operation, target))
        if failed:
            failed = False
            raise stop.StopError("Network failure at deadline")
        return remote(status="Stopped" if operation == "stop" or len(calls) == 4 else "Ready")

    monkeypatch.setattr(stop, "command", command)
    with pytest.raises(stop.StopError):
        stop.execute(record(), now=DUE)
    # A reboot loses Restart= state. OnBootSec invokes the same due-check again
    # even though the persisted calendar last-trigger says the timer fired.
    assert "OnBootSec=2min" in stop.timer(record())
    assert "stopped and confirmed" in stop.execute(record(), now=DUE + timedelta(hours=1))
    assert calls == [("get", ID), ("get", ID), ("stop", ID), ("get", ID)]
