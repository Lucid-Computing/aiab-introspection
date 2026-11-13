"""Run a signed agreement against the live box, one step at a time, through the auditor CLI.

The UI adds no verification logic: every
step is one call of ``python -m auditor.baked`` from the static part, and the screen shows
what that command printed or wrote. A run is a folder under the runs directory holding
plan/ (a frozen copy of the agreement as it was sent, signatures included), state.json (the
step list, and the plan version sent with its digest) and the CLI's own output files
(anchor, submission, admission, report). A background thread performs the steps; the page
re-reads state.json on each load.
"""

from __future__ import annotations

import copy
import json
import re
import secrets
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from aiab.audit.protocol import AuditError
from aiab.baked import plan_ir
from aiab.baked.bundle import bundle_files, file_digest, load_bundle
from aiab.baked.graph import select_records

from server.config import STATIC_DIR, Settings

RUN_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}")
# Runs this server process is performing. A run whose state says "running" but is not here
# was cut off by a restart. Runs have no time limit, so how long one has taken says nothing.
LIVE: set[str] = set()

STEPS = (
    (
        "verify",
        "Verify the plan",
        "Loads the agreement folder and checks both signatures against the keys the box "
        "pins. Offline. The run stops here if the plan is not jointly signed.",
    ),
    (
        "attest",
        "Attest the box",
        "Connects to the box with the Tinfoil SDK, checks its attestation against the "
        "release pins (image, policy) and saves its report-signing identity. "
        "This is the moment we learn which service will see the logs.",
    ),
    (
        "export",
        "Seal and sign the test export",
        "Takes the chosen records, seals each record's text to the box's encryption key "
        "from step 2 (only the box can open it) and signs them with the demo logger key, "
        "standing in for the real logger. The box will refuse records without this evidence. "
        "The plain records are deleted from the server once the export is written.",
    ),
    (
        "submit",
        "Submit the logs",
        "Sends the signed export and the signed plan to the box. The box verifies the plan's "
        "two signatures and restrictions, checks evidence, scope and size, admits the run "
        "and starts the plan with the real model. It keeps nothing of the plan afterwards.",
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


def _plan(folder: Path) -> dict | None:
    try:
        return json.loads((folder / "plan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def plan_source(folder: Path) -> str | None:
    """The log source the agreement's Log Source names (``config.source``), or None when
    the plan does not load. The caller decides whether the name is one of its sources."""
    plan = _plan(folder)
    try:
        return str(next(n for n in plan["nodes"] if n["type"] == "input_logs")["config"]["source"])
    except (TypeError, KeyError, StopIteration):
        return None


def plan_sessions(folder: Path) -> set[str]:
    """The sessions the agreement's Log Source names (``filter.sessions``); empty when it
    names none, which means every session of the source."""
    plan = _plan(folder)
    try:
        source = next(n for n in plan["nodes"] if n["type"] == "input_logs")
        return {str(x) for x in source["config"]["filter"].get("sessions") or ()}
    except (TypeError, KeyError, StopIteration, AttributeError):
        return set()


def plan_scope(folder: Path) -> dict | None:
    """What the agreement's Log Source step pins (agents, a time window), or None when it
    pins nothing or the plan does not load."""
    plan = _plan(folder)
    try:
        source = next(n for n in plan["nodes"] if n["type"] == "input_logs")
        scope = source["config"]["input_scope"]
    except (TypeError, KeyError, StopIteration):
        return None
    if not scope.get("agents") and not scope.get("window_start") and not scope.get("window_end"):
        return None
    return scope


def scope_notes(folder: Path, who: str, first: float, last: float) -> list[str]:
    """Why the agreement's input scope would make the box refuse these records."""
    scope = plan_scope(folder)
    if not scope:
        return []
    notes = []
    agents = scope.get("agents", [])
    if agents and who not in agents:
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


def preflight(settings: Settings, folder: Path | None) -> dict:
    """What a run of this agreement needs, and what is missing. Shown before starting."""
    release = {}
    missing = []
    plan_digest = None
    if folder is None:
        missing.append("no signed agreement")
    else:
        try:
            # Against the keys the box trusts now. After a key change an agreement signed
            # with the old keys is refused by the box, so it is refused here too.
            bundle = load_bundle(
                folder, json.loads(settings.trust_file.read_text(encoding="utf-8")), signed=True
            )
            plan_digest = bundle.digest
        except AuditError as exc:
            if str(exc) == "invalid_signature":
                missing.append("not signed by both parties")
            else:
                missing.append("signed agreement: " + str(exc))
        except (OSError, ValueError) as exc:
            missing.append("signed agreement: " + str(exc))
    if settings.release_file.is_file():
        release = json.loads(settings.release_file.read_text(encoding="utf-8"))
    else:
        missing.append(f"release pins: {settings.release_file}")
    for name in ("lab.token", "logger.key"):
        if not (settings.demo_keys_dir / name).is_file():
            missing.append(f"{name} in {settings.demo_keys_dir}")
    return {"release": release, "missing": missing, "ok": not missing, "plan_digest": plan_digest}


def sent_plan(settings: Settings, copy: Path, agreement: str) -> dict:
    """Which plan version a run sends: the digest of the frozen copy in the run folder and
    whether both signatures verify against the keys the box trusts, checked when the run
    starts. The run page links to this version, so a flag can be traced to its wiring."""
    entry = {"agreement": agreement, "digest": None, "signed": False, "checked": "at start"}
    try:
        bundle = load_bundle(
            copy, json.loads(settings.trust_file.read_text(encoding="utf-8")), signed=True
        )
        entry.update(digest=bundle.digest, signed=True)
    except AuditError as exc:
        entry["problem"] = exc.code
    except (OSError, ValueError) as exc:
        entry["problem"] = f"{type(exc).__name__}: {exc}"
    if entry["digest"] is None:
        try:
            entry["digest"] = file_digest(bundle_files(copy))
        except (AuditError, OSError, ValueError):
            pass
    return entry


def _derived_plan(settings: Settings, state: dict) -> dict | None:
    """The plan entry for a run saved before runs recorded one: the digest from the verify
    step, else from the copy in the run folder. Marked as derived."""
    verify = next((s for s in state["steps"] if s["key"] == "verify"), {})
    output = verify.get("output") or {}
    entry = {
        "agreement": state.get("agreement", ""),
        "digest": output.get("plan_digest"),
        "signed": bool(output.get("signatures_verified")),
        "checked": "derived",
    }
    if entry["digest"] is None:
        try:
            entry["digest"] = file_digest(bundle_files(settings.runs_dir / state["id"] / "plan"))
        except (AuditError, OSError, ValueError, KeyError):
            return None
    return entry


def _write(folder: Path, state: dict) -> None:
    tmp = folder / "state.json.tmp"
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(folder / "state.json")


def _cli(
    settings: Settings,
    folder: Path,
    *args: str,
    offline: bool = False,
    timeout: float | None = 400,
) -> dict | None:
    base = ["--folder", str(folder), "--trust", str(settings.trust_file), *args]
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
    done = subprocess.run(
        command, cwd=str(STATIC_DIR), capture_output=True, text=True, timeout=timeout
    )
    if done.returncode:
        raise RuntimeError(
            done.stderr.strip().splitlines()[-1] if done.stderr.strip() else "failed"
        )
    return json.loads(done.stdout) if done.stdout.strip() else None


def _execute(settings: Settings, folder: Path, state: dict) -> None:
    release, token = settings.release_file, settings.demo_keys_dir / "lab.token"
    logger = settings.demo_keys_dir / "logger.key"
    plan = folder / "plan"  # the agreement as it was when the run started
    fixture = folder / "records.jsonl"  # chosen sessions, written by start_run
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
                bundle = load_bundle(plan, json.loads(settings.trust_file.read_text()), signed=True)
            except AuditError as exc:
                raise RuntimeError(f"{exc.code} (the box's loader refused the plan)") from None
            except OSError as exc:
                raise RuntimeError(f"{type(exc).__name__}: {exc}") from None
            return {"plan_digest": bundle.digest, "signatures_verified": True}
        if key == "attest":
            try:
                _cli(settings, plan, "pin-identity", *connection, "--output", str(files["anchor"]))
            except RuntimeError as exc:
                if "model_unavailable" not in str(exc):
                    raise
                # The SDK's one code for every verification failure. The usual cause: the
                # release pins describe another release than the one the box runs.
                try:
                    tag = json.loads(release.read_text(encoding="utf-8")).get("tag", "?")
                except (OSError, ValueError):
                    tag = "?"
                raise RuntimeError(
                    f"the box could not be attested against the release pins ({tag}): they"
                    " describe another release than the one running, or Tinfoil could not"
                    " verify the box (model_unavailable)"
                ) from None
            anchor = json.loads(files["anchor"].read_text())
            return {"host": anchor["release"]["host"], "manifest": anchor["manifest"]}
        if key == "export":
            # Seal the records to the box attested in step 2 when it publishes an encryption
            # key (boxes before MAC-377 do not); the submission then holds ciphertext and the
            # plain records leave the server. Stands in for the logger, which will seal at
            # its end so this server never holds the text at all.
            identity = json.loads(files["anchor"].read_text()).get("identity", {})
            sealed = "encryption_key" in identity
            _cli(
                settings,
                plan,
                "mock-export",
                "--records",
                str(fixture),
                "--private",
                str(logger),
                "--submission-id",
                "ui-" + uuid.uuid4().hex,
                "--output",
                str(files["submission"]),
                *(("--seal-to", str(files["anchor"])) if sealed else ()),
                timeout=None,  # as long as the records need
            )
            submission = json.loads(files["submission"].read_text())
            fixture.unlink(missing_ok=True)
            return {
                "records": len(submission["records"]),
                "input_scope": submission["input_scope"],
                "sealed": sealed,
                "boot_id": identity.get("boot_id"),
            }
        if key == "submit":
            _cli(
                settings,
                plan,
                "submit",
                *connection,
                "--input",
                str(files["submission"]),
                "--output",
                str(files["admission"]),
                timeout=None,
            )
            admission.update(json.loads(files["admission"].read_text()))
            return admission
        if key == "report":
            _cli(
                settings,
                plan,
                "report",
                *connection,
                "--run-id",
                admission["run_id"],
                "--input-commitment",
                admission["input_commitment"],
                "--output",
                str(files["report"]),
                timeout=None,  # the box runs as long as the records need; it says when done
            )
            return json.loads(files["report"].read_text())
        if key == "offline":
            return _cli(
                settings,
                plan,
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
    agreement: str,
    folder: Path,
    records: list[dict],
    label: str | None = None,
    sent: dict | None = None,
) -> str:
    """Start a run of the agreement in ``folder`` on records chosen from the Logs page.

    The agreement is copied into the run folder first, signatures included: that copy is
    what is sent, so an edit made while the run is going changes nothing, and the report
    can always be verified again against exactly the plan it names. ``sent`` lists the
    sessions sent (``sent_sessions``) for the run page."""
    if not records:
        raise ValueError("no records chosen")
    run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"
    target = settings.runs_dir / run_id
    target.mkdir(parents=True, exist_ok=False)
    shutil.copytree(
        folder,
        target / "plan",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "history.jsonl"),
    )
    (target / "records.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    state = {
        "id": run_id,
        "started": _now(),
        "by": {"party": actor.get("party", ""), "user": actor.get("username", "")},
        "agreement": agreement,
        "transcript": label or agreement,
        "plan": sent_plan(settings, target / "plan", agreement),
        "input": sent,
        "status": "running",
        "steps": [{"key": k, "title": t, "explain": e, "status": "waiting"} for k, t, e in STEPS],
        "result": None,
    }
    _write(target, state)
    LIVE.add(run_id)
    threading.Thread(target=_tracked, args=(settings, target, state), daemon=True).start()
    return run_id


def _tracked(settings: Settings, folder: Path, state: dict) -> None:
    try:
        _execute(settings, folder, state)
    finally:
        LIVE.discard(state["id"])


def load_run(settings: Settings, run_id: str) -> dict | None:
    if not RUN_ID.fullmatch(run_id):
        return None
    path = settings.runs_dir / run_id / "state.json"
    if not path.is_file():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    if state["status"] == "running" and run_id not in LIVE:
        state["status"] = "interrupted"  # the server restarted while this run was going
    if "plan" not in state:
        state["plan"] = _derived_plan(settings, state)
    state["outcome"] = outcome(state)
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


# What each fact of a Report (records_report) means, for the run page.
METADATA_MEANING = {
    "record_count": "records that reached the report",
    "session_count": "sessions they belong to",
    "agent_count": "agents (who sent them)",
    "agent_ids": "the agents, at most 64",
    "kind_counts": "records of each kind",
    "first_ts": "earliest record",
    "last_ts": "latest record",
    "content_bytes": "bytes of text in all of them",
}
INCLUDED_KEYS = ("records_total", "records_included", "records")


def _shown(name: str, value: object) -> object:
    """A Report fact as one table cell: the counts per kind and the agent ids on one line."""
    if name == "kind_counts" and isinstance(value, dict):
        return " · ".join(f"{k} {v}" for k, v in value.items())
    if name == "agent_ids" and isinstance(value, list):
        return ", ".join(map(str, value)) or "none"
    if name in ("first_ts", "last_ts") and value == "":
        return "none"
    return value


def result_view(settings: Settings, state: dict) -> dict:
    """What the run page shows of the result: one row per released field with the plan's
    own description of it (read from the frozen plan copy in the run folder), and a few
    facts about the run. Empty rows when there is no verified, completed report. For a
    Report that includes records, ``disclosed`` holds them and how many of how many."""
    steps = {step["key"]: step for step in state["steps"]}
    out = state["outcome"]
    result = out.get("result") if out.get("verified") else None
    described: dict[str, str] = {}
    records_report = False
    try:
        plan = json.loads(
            (settings.runs_dir / state["id"] / "plan" / "plan.json").read_text(encoding="utf-8")
        )
        for node in plan.get("nodes", []):
            if node.get("type") == "final_report":
                for f in node.get("config", {}).get("fields", []):
                    described[f.get("name", "")] = f.get("description", "")
            elif node.get("type") == "records_report":
                records_report = True
                described.update(METADATA_MEANING)
    except (OSError, ValueError, AttributeError):
        pass
    items = list(result.items()) if isinstance(result, dict) else []
    if records_report:  # the signed report sorts its keys; show the facts in the builder's order
        order = {k: i for i, k in enumerate(plan_ir.METADATA)}
        items.sort(key=lambda kv: order.get(kv[0], len(order)))
    rows = [
        {
            "name": k,
            "value": _shown(k, v) if records_report else v,
            "description": described.get(k, ""),
        }
        for k, v in items
        if not (records_report and k in INCLUDED_KEYS)
    ]
    disclosed = None
    if records_report and isinstance(result, dict) and "records" in result:
        disclosed = {
            "included": result.get("records_included"),
            "total": result.get("records_total"),
            "items": [r for r in result.get("records") or () if isinstance(r, dict)],
        }
    manifest = (steps.get("attest", {}).get("output") or {}).get("manifest") or {}
    seconds = None
    try:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        seconds = int(
            (
                datetime.strptime(state["finished"], fmt) - datetime.strptime(state["started"], fmt)
            ).total_seconds()
        )
    except (KeyError, ValueError, TypeError):
        pass
    return {
        "rows": rows,
        "disclosed": disclosed,
        "records": (steps.get("export", {}).get("output") or {}).get("records"),
        "seconds": seconds,
        "model": (manifest.get("model_binding") or {}).get("id"),
        "image": manifest.get("image_digest"),
    }


def outcome(state: dict) -> dict:
    """Derive the verdict from the report, including runs saved by older UI versions."""
    steps = {step["key"]: step for step in state["steps"]}
    report = (steps.get("report", {}).get("output") or {}).get("report", {})
    verified = steps.get("offline", {}).get("status") == "done"
    result = report.get("result") if report.get("status") == "completed" else None
    flagged = result.get("flagged") if isinstance(result, dict) else None
    if report.get("status") == "failed":
        title = "Execution failed — no verdict"
    elif report.get("status") == "completed" and verified:
        title = ("Flagged" if flagged else "Not flagged") if type(flagged) is bool else "Completed"
    elif state["status"] == "failed":
        title = "Run stopped — no verified verdict"
    elif state["status"] == "interrupted":
        title = "Run interrupted — no verified verdict"
    else:
        title = "Running — no verified verdict yet"
    code = report.get("error")
    help_text = {
        "execution_failed": (
            "The executor could not finish the plan. This older report has no stage details; "
            "use the run ID to investigate. Do not interpret this as an all-clear."
        ),
        "model_unavailable": (
            "The confidential model could not be reached or verified. Check the inference "
            "service before retrying."
        ),
        "input_too_large": (
            "One model question did not fit the model's context window even after splitting "
            "long items: a step that asks one question over all its input (a Value output), "
            "or a prompt that is too long by itself. Review that step."
        ),
        "invalid_output": (
            "The plan returned a result outside its agreed report fields, or larger than its"
            " byte cap. Review the report, then re-sign the corrected plan."
        ),
        "plan_start_failed": (
            "The plan worker failed to start or to compile the plan's Python steps. Check the "
            "runtime and the plan."
        ),
        "plan_query_failed": (
            "The plan failed while selecting records: in its Log Source, a Filter's rules, or "
            "an AND or OR block."
        ),
        "plan_classify_failed": (
            "The plan failed in a classifier or model step (an LLM Filter is one): the model's "
            "answers did not fit, or an item could not be prepared."
        ),
        "plan_metric_failed": (
            "A Python step failed or returned a value that does not match its declared output. "
            "Check its code against the records it receives, including an empty input."
        ),
        "plan_report_failed": "The plan failed while producing the report from its input.",
        "execution_timeout": (
            "The plan used an hour of processor time without finishing; it was stopped. Check "
            "Python steps for a loop that never ends, and Filter regexes for a slow pattern "
            "over long records."
        ),
        "model_output_truncated": (
            "The classifier exhausted its output-token budget before completing its answer. "
            "Review the model budget; no verdict was produced."
        ),
        "model_output_invalid": (
            "The classifier did not return the required numeric score. Review the prompt and "
            "model response contract; no verdict was produced."
        ),
    }.get(code, "")
    return {
        "title": title,
        "execution": report.get("status"),
        "verified": verified,
        "result": result,
        "error": code,
        "help": help_text,
    }


def sent_sessions(folder: Path, source: str, sessions: list[dict], records: list[dict]) -> dict:
    """What a run sends, by session: id, title, the records sent, and how many of them
    the agreement's Log Source keeps. The filter is the box's own (``select_records``) run
    on the records' metadata only: kind, agent, session. When the filter also matches
    keywords, ``kept`` is None: that reads the text, and which records pass is the box's
    to know. Written into the run's state at start; the records themselves are deleted."""
    plan = _plan(folder)
    node = None
    try:
        node = next(n for n in plan["nodes"] if n["type"] == "input_logs")
    except (TypeError, KeyError, StopIteration):
        pass
    keywords = bool(node and any(node["config"]["filter"].get("keywords") or ()))
    kept: list[dict] | None = None
    if node is not None and not keywords:
        meta = copy.deepcopy(node)
        meta["config"]["filter"]["keywords"] = []
        meta["config"]["max_records"] = len(records)  # over the limit is a failure, not a drop
        try:
            kept = select_records(meta, records)
        except (KeyError, TypeError, ValueError):
            kept = None
    out = []
    for s in sessions:
        sent = sum(1 for r in records if r.get("session_id") == s["id"])
        keeps = None if kept is None else sum(1 for r in kept if r["session_id"] == s["id"])
        out.append({**s, "records": sent, "kept": keeps})
    return {
        "source": source,
        "sessions": out,
        "filter": "keywords" if keywords else "metadata" if kept is not None else "unknown",
    }


def run_counts(settings: Settings, state: dict) -> dict | None:
    """Sessions sent, dropped and classified for the run page. Sent and dropped come from
    what the UI sent (``sent_sessions``); what reaches the classifier is worked out from the
    frozen plan when the classifier reads the Log Source directly. With a Filter, AND or OR
    between them the box decides which records go on, so it is not worked out here.
    Classified comes only from fields of the verified report that count the classifier's
    items. Those are items, not sessions: a session longer than the classifier's window is
    several items. None for runs saved before runs recorded their sessions."""
    sent = state.get("input")
    if not sent:
        return None
    sessions = sent["sessions"]
    plan = _plan(settings.runs_dir / state["id"] / "plan") or {}
    nodes = {n.get("id"): n for n in plan.get("nodes") or () if isinstance(n, dict)}

    def source_of(node: dict | None, port: str | None = None) -> dict | None:
        try:
            ref = node["inputs"][port] if port else next(iter(node["inputs"].values()))
            return nodes.get(plan_ir.split_ref(ref)[0])
        except (TypeError, KeyError, StopIteration, ValueError):
            return None

    report = next((n for n in nodes.values() if n.get("type") == "final_report"), None)
    records_report = any(n.get("type") == "records_report" for n in nodes.values())
    classifier = source_of(report, plan_ir.REPORT_INPUT)
    if not classifier or classifier.get("type") != "classifier":
        classifier = None
    cfg = (classifier or {}).get("config") or {}
    direct = bool(classifier) and (source_of(classifier) or {}).get("type") == "input_logs"
    known = sent["filter"] == "metadata"
    dropped = [s for s in sessions if known and not s["kept"]]
    item_count = None
    if known and direct:
        window = max(1, int(cfg.get("window") or 1))
        per = [s["kept"] for s in sessions if s["kept"]]
        item_count = (
            sum(-(-k // window) for k in per) if cfg.get("group_by") == "session" else sum(per)
        )
    steps = {s.get("key"): s for s in state.get("steps") or ()}
    out = state.get("outcome") or {}
    result = (
        out.get("result") if out.get("verified") and out.get("execution") == "completed" else None
    )
    fields = ((report or {}).get("config") or {}).get("fields") or () if classifier else ()
    counts = []
    for f in fields:
        head, _, label = str(f.get("from", f.get("name"))).partition(".")
        if head == "count":
            value = result.get(f["name"]) if isinstance(result, dict) else None
            counts.append({"name": f["name"], "label": label or None, "value": value})
    privacy = ((report or {}).get("config") or {}).get("privacy") or {}
    return {
        "submitted": (steps.get("submit") or {}).get("status") == "done",
        "source": sent.get("source"),
        "sessions": sessions,
        "sent": len(sessions),
        "records": sum(s["records"] for s in sessions),
        "filter": sent["filter"],
        "dropped": dropped,
        "reach": len(sessions) - len(dropped) if known else None,
        "direct": direct,
        "item_count": item_count,
        "group_by": cfg.get("group_by"),
        "window": cfg.get("window"),
        "threshold": cfg.get("threshold"),
        "counts": counts,
        "reported": result is not None,
        "min_count": privacy.get("min_count") or 1,
        # A Report (records_report) reads records, not a classifier's items.
        "records_report": records_report,
    }
