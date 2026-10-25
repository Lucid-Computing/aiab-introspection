"""Run the deployed plan against the live box, one step at a time, through the auditor CLI.

This mirrors static/demo/audit/walkthrough.py. The UI adds no verification logic: every
step is one call of ``python -m auditor.baked`` from the static part, and the screen shows
what that command printed or wrote. A run is a folder under the runs directory holding
state.json (the step list) and the CLI's own output files (anchor, submission, admission,
report). A background thread performs the steps; the page re-reads state.json on each load.
"""

from __future__ import annotations

import json
import re
import secrets
import subprocess
import sys
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from aiab.audit.protocol import AuditError
from aiab.baked.bundle import load_bundle

from server.config import STATIC_DIR, Settings

BOX_TRUST = STATIC_DIR / "aiab" / "baked" / "trust.json"  # the keys the box itself pins
RUN_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}")
STALE_SECONDS = 15 * 60  # a step still "running" after this long was interrupted

STEPS = (
    (
        "verify",
        "Verify the plan",
        "Loads the deployed plan folder and checks both signatures against the keys the box "
        "pins. Offline. The run stops here if the plan is not jointly signed.",
    ),
    (
        "attest",
        "Attest the box",
        "Connects to the box with the Tinfoil SDK, checks its attestation against the "
        "release pins (image, policy, plan digest) and saves its report-signing identity. "
        "This is the moment we learn which service will see the logs.",
    ),
    (
        "export",
        "Sign the test export",
        "Takes the chosen records and signs them with the demo logger key, standing in for "
        "the real logger. The box will refuse records without this evidence.",
    ),
    (
        "submit",
        "Submit the logs",
        "Sends the signed export to the box. The box checks evidence, scope and size, admits "
        "it and starts running the plan with the real model.",
    ),
    (
        "report",
        "Wait for the signed report",
        "Polls the box until the plan has finished. The report is signed with the identity "
        "saved in step 2 and contains only the typed answer.",
    ),
    (
        "offline",
        "Verify the report offline",
        "Verifies the saved report once more in a process with networking disabled, so the "
        "verdict does not depend on the box being reachable.",
    ),
)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def transcripts(settings: Settings) -> list[str]:
    folder = settings.deployed_plan_dir / "tests" / "transcripts"
    return sorted(p.name for p in folder.glob("*.jsonl")) if folder.is_dir() else []


def plan_scope(settings: Settings) -> dict | None:
    """The deployed plan's input_scope block, or None when the plan does not load."""
    try:
        return json.loads((settings.deployed_plan_dir / "plan.json").read_text(encoding="utf-8"))[
            "input_scope"
        ]
    except (OSError, ValueError, KeyError):
        return None


def scope_notes(settings: Settings, who: str, first: float, last: float) -> list[str]:
    """Why the deployed plan's input scope would make the box refuse these records."""
    scope = plan_scope(settings)
    if not scope:
        return []
    notes = []
    agents = scope.get("agents", [])
    if who not in agents:
        notes.append(f"agent '{who}' is not in the plan's scope ({', '.join(agents)})")
    try:
        start = datetime.fromisoformat(scope["window_start"]).timestamp()
        end = datetime.fromisoformat(scope["window_end"]).timestamp()
    except (KeyError, ValueError, TypeError):
        return notes
    if first < start or last >= end:
        notes.append(
            f"outside the plan's window {scope['window_start'][:10]} to {scope['window_end'][:10]}"
        )
    return notes


def check_limits(settings: Settings, records: list[dict]) -> None:
    """Refuse here what the box would refuse as input_too_large."""
    try:
        limits = json.loads((settings.deployed_plan_dir / "plan.json").read_text(encoding="utf-8"))[
            "limits"
        ]
    except (OSError, ValueError, KeyError):
        return
    size = len(json.dumps(records, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    if len(records) > limits["max_records_per_run"]:
        raise ValueError(
            f"{len(records)} records, the plan allows {limits['max_records_per_run']} per run;"
            " pick fewer sessions"
        )
    if size > limits["max_input_utf8_bytes"]:
        raise ValueError(
            f"{size // 1024} KB of records, the plan allows"
            f" {limits['max_input_utf8_bytes'] // 1024} KB per run; pick fewer sessions"
        )


def preflight(settings: Settings) -> dict:
    """What a run needs, and what is missing. Shown on the Runs screen before starting."""
    release = {}
    missing = []
    plan_digest = None
    try:
        bundle = load_bundle(
            settings.deployed_plan_dir, json.loads(BOX_TRUST.read_text()), signed=True
        )
        plan_digest = bundle.digest
    except (OSError, ValueError, AuditError) as exc:
        missing.append("signed deployed plan: " + str(exc))
    if settings.release_file.is_file():
        release = json.loads(settings.release_file.read_text(encoding="utf-8"))
    else:
        missing.append(f"release pins: {settings.release_file}")
    for name in ("lab.token", "logger.key"):
        if not (settings.demo_keys_dir / name).is_file():
            missing.append(f"{name} in {settings.demo_keys_dir}")
    if release and plan_digest and release.get("plan_digest") != plan_digest:
        missing.append("deployed plan digest differs from release pins")
    return {"release": release, "missing": missing, "ok": not missing, "plan_digest": plan_digest}


def _write(folder: Path, state: dict) -> None:
    tmp = folder / "state.json.tmp"
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(folder / "state.json")


def _cli(settings: Settings, *args: str, offline: bool = False) -> dict | None:
    base = ["--folder", str(settings.deployed_plan_dir), "--trust", str(BOX_TRUST), *args]
    if offline:
        # The same CLI, with sockets disabled, as the walkthrough does for the offline check.
        code = (
            "import socket, sys; from auditor.baked import main; "
            "socket.socket = lambda *a, **k: (_ for _ in ()).throw("
            "RuntimeError('Network disabled for offline verification')); main(sys.argv[1:])"
        )
        command = [sys.executable, "-c", code, *base]
    else:
        command = [sys.executable, "-m", "auditor.baked", *base]
    done = subprocess.run(command, cwd=str(STATIC_DIR), capture_output=True, text=True, timeout=400)
    if done.returncode:
        raise RuntimeError(
            done.stderr.strip().splitlines()[-1] if done.stderr.strip() else "failed"
        )
    return json.loads(done.stdout) if done.stdout.strip() else None


def _execute(settings: Settings, folder: Path, state: dict) -> None:
    release, token = settings.release_file, settings.demo_keys_dir / "lab.token"
    logger = settings.demo_keys_dir / "logger.key"
    fixture = folder / "records.jsonl"  # chosen sessions, written by start_run
    if not fixture.exists():
        fixture = settings.deployed_plan_dir / "tests" / "transcripts" / state["transcript"]
    files = {
        "anchor": folder / "anchor.json",
        "submission": folder / "submission.json",
        "admission": folder / "admission.json",
        "report": folder / "report.json",
    }
    connection = ("--release", str(release), "--token-file", str(token))
    admission: dict = {}

    def run_step(index: int) -> dict | None:
        key = state["steps"][index]["key"]
        if key == "verify":
            # The CLI hides the cause of a load failure behind one code; call the same box
            # function here so a missing file or a bad key shows as itself.
            try:
                bundle = load_bundle(
                    settings.deployed_plan_dir, json.loads(BOX_TRUST.read_text()), signed=True
                )
            except AuditError as exc:
                raise RuntimeError(
                    f"{exc.code} (the box's loader refused {settings.deployed_plan_dir})"
                ) from None
            except OSError as exc:
                raise RuntimeError(f"{type(exc).__name__}: {exc}") from None
            return {"plan_digest": bundle.digest, "signatures_verified": True}
        if key == "attest":
            _cli(settings, "pin-identity", *connection, "--output", str(files["anchor"]))
            anchor = json.loads(files["anchor"].read_text())
            return {"host": anchor["release"]["host"], "manifest": anchor["manifest"]}
        if key == "export":
            _cli(
                settings,
                "mock-export",
                "--records",
                str(fixture),
                "--private",
                str(logger),
                "--submission-id",
                "ui-" + uuid.uuid4().hex,
                "--output",
                str(files["submission"]),
            )
            submission = json.loads(files["submission"].read_text())
            return {"records": len(submission["records"]), "input_scope": submission["input_scope"]}
        if key == "submit":
            _cli(
                settings,
                "submit",
                *connection,
                "--input",
                str(files["submission"]),
                "--output",
                str(files["admission"]),
            )
            admission.update(json.loads(files["admission"].read_text()))
            return admission
        if key == "report":
            _cli(
                settings,
                "report",
                *connection,
                "--run-id",
                admission["run_id"],
                "--input-commitment",
                admission["input_commitment"],
                "--output",
                str(files["report"]),
            )
            return json.loads(files["report"].read_text())
        if key == "offline":
            return _cli(
                settings,
                "verify-offline",
                "--release",
                str(release),
                "--anchor",
                str(files["anchor"]),
                "--report",
                str(files["report"]),
                "--run-id",
                admission["run_id"],
                "--input-commitment",
                admission["input_commitment"],
                offline=True,
            )
        return None

    for index, step in enumerate(state["steps"]):
        step["status"], step["started"] = "running", _now()
        _write(folder, state)
        try:
            step["output"] = run_step(index)
            step["status"] = "done"
        except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
            step["status"], step["error"] = "failed", str(exc)[-600:]
            state["status"] = "failed"
            step["finished"] = _now()
            _write(folder, state)
            return
        step["finished"] = _now()
        _write(folder, state)
    report_step = next((s for s in state["steps"] if s["key"] == "report"), {})
    report = report_step.get("output") or {}
    state["result"] = {
        "run_id": admission.get("run_id"),
        "flagged": report.get("report", {}).get("result", {}).get("flagged"),
        "status": report.get("report", {}).get("status"),
    }
    state["status"] = "done"
    state["finished"] = _now()
    _write(folder, state)


def start_run(
    settings: Settings,
    actor: dict[str, str],
    transcript: str | None = None,
    records: list[dict] | None = None,
    label: str | None = None,
) -> str:
    """Start a run on a test transcript, or on records chosen from the Logs page."""
    if records is None and transcript not in transcripts(settings):
        raise ValueError("unknown transcript")
    if records is not None:
        if not records:
            raise ValueError("no records chosen")
        check_limits(settings, records)
    run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"
    folder = settings.runs_dir / run_id
    folder.mkdir(parents=True, exist_ok=False)
    if records is not None:
        (folder / "records.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
        )
    state = {
        "id": run_id,
        "started": _now(),
        "by": {"party": actor.get("party", ""), "user": actor.get("username", "")},
        "transcript": label or transcript,
        "status": "running",
        "steps": [{"key": k, "title": t, "explain": e, "status": "waiting"} for k, t, e in STEPS],
        "result": None,
    }
    _write(folder, state)
    threading.Thread(target=_execute, args=(settings, folder, state), daemon=True).start()
    return run_id


def load_run(settings: Settings, run_id: str) -> dict | None:
    if not RUN_ID.fullmatch(run_id):
        return None
    path = settings.runs_dir / run_id / "state.json"
    if not path.is_file():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    if state["status"] == "running":
        started = datetime.strptime(state["started"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        if (datetime.now(UTC) - started).total_seconds() > STALE_SECONDS:
            state["status"] = "interrupted"  # the server restarted while this run was going
    return state


def list_runs(settings: Settings) -> list[dict]:
    if not settings.runs_dir.is_dir():
        return []
    runs = []
    for child in sorted(settings.runs_dir.iterdir(), reverse=True):
        state = load_run(settings, child.name)
        if state:
            runs.append(state)
    return runs
