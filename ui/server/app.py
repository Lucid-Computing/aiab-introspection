"""The agreement UI: a FastAPI front end over the plan folders and the box's read-only endpoints."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from server import deployed, deploystatus, editing, plan_chat, proxylogs, publish, runs
from server import plan_view as pv
from server.auth import SessionSigner, authenticate
from server.box import box_status
from server.config import UI_ROOT, Settings, load_settings
from server.pipeline import PARTIES

settings = load_settings()
signer = SessionSigner(settings.session_secret)


@asynccontextmanager
async def lifespan(_: FastAPI):
    proxylogs.start_prefetch(settings)  # keeps the Logs page's copy of the proxy fresh
    asyncio.ensure_future(deploystatus.warm_up(settings))  # first Agreements load is fast too
    refresher = asyncio.ensure_future(deploystatus.refresh_loop())  # GitHub answers stay warm
    if settings.auto_pull:
        publish.start_auto_pull(settings.git_dir)  # the deployed plan shown is the merged one
    yield
    refresher.cancel()


app = FastAPI(
    title="AIAB agreement UI", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
)
app.mount("/assets", StaticFiles(directory=UI_ROOT / "assets"), name="assets")
templates = Jinja2Templates(directory=UI_ROOT / "server" / "templates")
COOKIE = "aiab_ui"


def pretty_json(text: str) -> str:
    """Indent JSON for display; leave anything else as it is."""
    import json

    try:
        return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except (ValueError, TypeError):
        return text


templates.env.filters["pretty_json"] = pretty_json


def preview(text: str, chars: int = 420, lines: int = 6) -> str:
    """The start of a long text: at most `lines` lines and `chars` characters."""
    head = "\n".join(text.split("\n")[:lines])[:chars]
    return head.rstrip() + (" …" if len(head) < len(text) else "")


def is_long(text: str) -> bool:
    return len(text) > 700 or text.count("\n") > 10


_INLINE = [
    (re.compile(r"`([^`\n]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*(.+?)\*\*"), r"<strong>\1</strong>"),
]


def _inline(line: str) -> str:
    out = str(escape(line))
    for pattern, repl in _INLINE:
        out = pattern.sub(repl, out)
    return out


def md(text: str) -> Markup:
    """Light, safe markdown for model replies: bold, inline code, headings, bullets, fences.
    Everything is HTML-escaped first; only these few tags are produced."""
    out: list[str] = []
    para: list[str] = []
    in_code = False
    in_list = False

    def flush() -> None:
        nonlocal in_list
        if para:
            out.append("<p>" + "<br>".join(para) + "</p>")
            para.clear()
        if in_list:
            out.append("</ul>")
            in_list = False

    for line in text.split("\n"):
        if line.strip().startswith("```"):
            if in_code:
                out.append("</code></pre>")
            else:
                flush()
                out.append('<pre class="md-code"><code>')
            in_code = not in_code
            continue
        if in_code:
            out.append(str(escape(line)))
            continue
        s = line.rstrip()
        bullet = re.match(r"^\s*[-*•]\s+(.*)", s)
        heading = re.match(r"^#{1,6}\s+(.*)", s)
        if bullet:
            if para:
                out.append("<p>" + "<br>".join(para) + "</p>")
                para.clear()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append("<li>" + _inline(bullet.group(1)) + "</li>")
        elif heading:
            flush()
            out.append('<p class="md-h">' + _inline(heading.group(1)) + "</p>")
        elif not s:
            flush()
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            para.append(_inline(s))
    if in_code:
        out.append("</code></pre>")
    flush()
    return Markup("\n".join(out))


templates.env.filters["preview"] = preview
templates.env.filters["hhmm"] = lambda t: __import__("time").strftime(
    "%H:%M", __import__("time").localtime(t)
)
templates.env.filters["md"] = md
templates.env.tests["long"] = is_long
CHANGE_KINDS = {  # filter name -> history "kind" values it covers
    "settings": ("settings",),
    "code": ("code",),
    "prompt": ("prompt",),
    "signing": ("signature", "key"),
    "deploy": ("deploy", "created"),
}
FROZEN = "A valid signature exists for this version. Use Edit on the Agreements screen first."
EDIT_WARNING = (
    "Editing makes a new version of the plan. Both parties must sign it again, "
    "and any existing signature is removed now. Continue?"
)

STAGES: list[tuple[str, str, list[str]]] = [
    (
        "Draft",
        "Edit the settings, the code and the prompt. Every save changes the plan digest.",
        [
            "Create an agreement with a name and the question the classifier answers, "
            "from a minimal template or as a copy of the deployed one.",
            "Open it to see the four steps the box runs, in order: "
            "group the records into queries, classify each query, turn scores into a verdict, "
            "emit the typed answer.",
            "Edit a step: its settings in plan.json, its code in plan.py, and for classify "
            "the prompt. Saves are written straight to those files, and edits made in the "
            "files show up here on reload.",
            "Every save is a new version with a new digest. The Changes list on the plan "
            "shows who changed what, lab or auditor.",
        ],
    ),
    (
        "Freeze and sign",
        "Each party signs the digest on its own machine and pastes the signature.",
        [
            "The Sign screen shows the version's digest and the one-line signing command.",
            "One openssl command signs that digest with your private key and prints one "
            "line; paste it and the UI verifies it against the public key on file for your "
            "party, which you submit on the same screen.",
            "The first valid signature locks editing. To change anything after that, remove "
            "the signatures and both parties sign the new version.",
        ],
    ),
    (
        "Build and deploy",
        "Deploy selects the agreement; the build and the operator do the rest.",
        [
            "Deploy copies the agreement into plan/, the folder the image is built from.",
            "Commit and push; CI re-checks the digest, both signatures and the restrictions, "
            "then merges and tags.",
            "The image is built twice from clean inputs, both digests are published, and the "
            "operator deploys the release to the confidential VM.",
        ],
    ),
    (
        "Verify and run",
        "Check the box runs this plan, then submit runs and read signed reports.",
        [
            "The box reports the image digest and plan digest it runs; the Agreements screen "
            "compares the plan digest with the deployed agreement.",
            "Attestation is the hardware's signed statement of the same thing, for an "
            "independent check with the auditor's own tools.",
            "Submitting runs and reading reports comes after, in a later version of this UI.",
        ],
    ),
]
AFTER_SIGNING: list[str] = [
    "CI re-checks the plan: digest, both signatures, the box's restrictions.",
    "The pull request merges and the commit is tagged.",
    "The image is built twice from clean inputs; the two digests must match.",
    "The release publishes the image digest and the plan digest.",
    "The operator deploys the tagged release to the confidential VM.",
    "The box verifies the signatures again at start-up and reports both digests.",
    "The attestation check confirms the running image is the published one.",
]


# --------------------------------------------------------------------------- helpers


def user_of(request: Request) -> dict[str, str] | None:
    return signer.read(request.cookies.get(COOKIE))


def to_login(request: Request) -> RedirectResponse:
    return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)


def back(path: str, *, msg: str | None = None, err: str | None = None) -> RedirectResponse:
    separator = "&" if "?" in path else "?"
    if msg:
        path += f"{separator}msg={quote(msg)}"
    elif err:
        path += f"{separator}err={quote(err)}"
    return RedirectResponse(path, status_code=303)


def render(request: Request, template: str, **context: Any) -> Response:
    context.setdefault("chat_enabled", bool(settings.anthropic_api_key))
    context.setdefault("msg", request.query_params.get("msg"))
    context.setdefault("err", request.query_params.get("err"))
    context.update(
        user=user_of(request),
        settings=settings,
        active=template.removesuffix(".html"),
        parties=PARTIES,
        edit_warning=EDIT_WARNING,
        asset_version=int((UI_ROOT / "assets" / "aiab.css").stat().st_mtime),
    )
    return templates.TemplateResponse(request, template, context)


def saved(actor: dict[str, str] | None) -> None:
    """After a write to an agreement folder: push the drafts if this deployment asked for it."""
    if settings.push_drafts:
        publish.schedule_drafts_push(settings.git_dir, actor)


_locks: dict[str, asyncio.Lock] = {}


def _lock(aid: str) -> asyncio.Lock:
    """Saves on one agreement happen one at a time: read the version, check it, write."""
    return _locks.setdefault(aid, asyncio.Lock())


def _held(aid: str, actor: dict) -> str | None:
    """Someone else is editing: a save from anyone else is refused."""
    other = editing.blocked_by(aid, actor)
    return editing.describe(other) + ". Your change was not saved." if other else None


def _stale(view: pv.PlanView, expect: str) -> str | None:
    """Several people edit the same agreement: a save made from a page that showed an
    older version is refused instead of silently overwriting the newer one."""
    if not expect or not view.digest or expect == view.digest:
        return None
    last = view.history[0] if view.history else None
    who = f" by {last['user']} ({last['party']})" if last else ""
    return (
        f"Someone else changed this agreement{who} since you opened the page, so your"
        " change was not saved. Reload, look at the new version, then apply your change."
    )


def load_view(target: Settings) -> tuple[pv.PlanView | None, str | None]:
    try:
        return pv.load_plan_view(target), None
    except Exception as exc:  # noqa: BLE001 - show the broken plan instead of a 500
        return None, f"{type(exc).__name__}: {exc}"


READ_ONLY = (
    "The deployed plan is read only: it is what the box runs. Edit, sign and deploy the"
    " agreement it was made from, under agreements/."
)


def agreement(
    request: Request, aid: str, write: bool = False
) -> tuple[Settings | None, Response | None]:
    """Resolve an agreement id, or the redirect to send instead. With ``write``, the deploy
    slot (id "plan") is refused: every change happens in an agreement folder."""
    if user_of(request) is None:
        return None, to_login(request)
    if write and aid == "plan":
        return None, back("/agreements", err=READ_ONLY)
    target = pv.for_agreement(settings, aid)
    if target is None:
        return None, back("/agreements", err=f"No agreement called {aid}")
    return target, None


# --------------------------------------------------------------------------- auth


@app.get("/login")
async def login_form(request: Request) -> Response:
    return render(request, "login.html", next=request.query_params.get("next", "/"))


@app.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
) -> Response:
    account = authenticate(settings.accounts_file, username.strip(), password)
    if account is None:
        return render(request, "login.html", next=next, err="Unknown account or wrong password.")
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(COOKIE, signer.issue(account), httponly=True, samesite="lax")
    return response


@app.post("/logout")
async def logout() -> Response:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE)
    return response


# --------------------------------------------------------------------------- top level


@app.get("/")
async def index(request: Request) -> Response:
    if user_of(request) is None:
        return to_login(request)
    return RedirectResponse("/how", status_code=303)


@app.get("/how")
async def how(request: Request) -> Response:
    if user_of(request) is None:
        return to_login(request)
    return render(request, "how.html", stages=STAGES, after=AFTER_SIGNING)


@app.get("/logs")
async def logs_page(request: Request) -> Response:
    if user_of(request) is None:
        return to_login(request)
    q = request.query_params.get("q", "").strip()
    who = request.query_params.get("who", "").strip()
    wanted = request.query_params.get("session", "")
    wanted_req = request.query_params.get("req", "")
    records = proxylogs.load_records(settings)
    all_sessions = proxylogs.sessions(records)
    whos = sorted({s.who for s in all_sessions})
    shown = [s for s in all_sessions if not who or s.who == who]
    if q:
        needle = q.lower()
        shown = [s for s in shown if needle in s.title.lower()]
    current = next((s for s in all_sessions if s.id == wanted), None) or (
        shown[0] if shown else None
    )
    req = None
    if current:
        req = (
            next((r for r in current.requests if r.id == wanted_req), None) or current.requests[-1]
        )
    return render(
        request,
        "logs.html",
        shown=shown,
        current=current,
        req=req,
        q=q,
        who=who,
        whos=whos,
        total_requests=len(records),
        fetch=proxylogs.status(),
    )


@app.get("/logs/raw/{rid}")
async def logs_raw(request: Request, rid: str) -> Response:
    """The recorded request as the proxy stored it, with the JSON bodies unpacked."""
    if user_of(request) is None:
        return to_login(request)
    entry = next((e for e in proxylogs.load_records(settings) if str(e.get("id")) == rid), None)
    if entry is None:
        return Response("no such request", status_code=404)
    shown = dict(entry)
    for key in ("request_raw", "response_raw"):
        if isinstance(shown.get(key), dict) and isinstance(shown[key].get("text"), str):
            try:
                shown[key] = json.loads(shown[key]["text"])
            except ValueError:
                shown[key] = shown[key]["text"]
    return Response(json.dumps(shown, indent=2, ensure_ascii=False), media_type="application/json")


@app.post("/logs/sync")
async def logs_sync(request: Request) -> Response:
    if user_of(request) is None:
        return to_login(request)
    try:
        result = proxylogs.sync_now(settings)
    except proxylogs.ProxyError as exc:
        return back("/logs", err=str(exc))
    return back(
        "/logs",
        msg=f"Fetched {result['new']} new segment(s); {result['segments']} on disk, "
        f"{result['live_entries']} live request(s) not sealed yet.",
    )


@app.get("/agreements")
async def agreements(request: Request) -> Response:
    if user_of(request) is None:
        return to_login(request)
    return await _agreements_page(request)


async def _agreements_page(request: Request, **extra) -> Response:
    """Agreements (editable, under agreements/), the deployed plan (read only, plan/), the
    box, and the deployment in flight if there is one."""
    deployed = pv.deployed_plan(settings)
    box = await box_status(settings.box_url, deployed.get("digest") or "")
    rows = pv.list_agreements(settings)
    box_digest = (box.get("manifest") or {}).get("plan_digest")
    current, keys = await asyncio.gather(_current_deployment(box), _keys(box))
    for r in rows:
        r["editor"] = editing.holder(r["id"])
        r["running"] = bool(r["digest"]) and r["digest"] == box_digest
        r["deploying"] = bool(current) and current["digest"] == r["digest"] and current["state"]
    return render(
        request,
        "agreements.html",
        rows=rows,
        deployed=deployed,
        box=box,
        keys=keys,
        current=current,
        busy=bool(current and current["state"] and current["state"]["busy"]),
        **extra,
    )


@app.get("/deployed")
async def deployed_files(request: Request) -> Response:
    """The deploy slot as it is: every file under plan/, read only (the Read button)."""
    if user_of(request) is None:
        return to_login(request)
    deployed = pv.deployed_plan(settings)
    box = await box_status(settings.box_url, deployed.get("digest") or "")
    return render(
        request, "deployed.html", deployed=deployed, box=box, files=pv.plan_files(settings)
    )


async def _in_flight(aid: str) -> str | None:
    """Why this agreement may not be touched right now: it is being deployed."""
    current = await _current_deployment(await box_status(settings.box_url, ""))
    if current and current["id"] == aid and current["state"] and current["state"]["busy"]:
        return f"{current['name']} is being deployed ({current['state']['label']}); wait for it."
    return None


def _deploy_marker() -> Path:
    return settings.agreements_dir / ".deploying.json"


def _remember_deploy(aid: str, plan_id: str, digest: str, phase: str = "pushed", **more) -> None:
    """The deployment in flight, so every visitor's page can follow it. ``phase`` is
    pushing (the branch is on its way), pushed (GitHub takes over) or failed."""
    try:
        settings.agreements_dir.mkdir(parents=True, exist_ok=True)
        _deploy_marker().write_text(
            json.dumps(
                {
                    "id": aid,
                    "name": plan_id,
                    "digest": digest,
                    "phase": phase,
                    "at": time.time(),
                    **more,
                }
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


async def _current_deployment(box: dict) -> dict | None:
    """The last deployment started from this UI, with its live state from GitHub.
    None when there was none, or when its pull request was closed without merging."""
    try:
        marker = json.loads(_deploy_marker().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    box_digest = (box.get("manifest") or {}).get("plan_digest")
    phase = marker.get("phase", "pushed")
    if phase == "pushing":
        state = {"pr": None, "label": "deploying · pushing the branch", "tone": "now", "busy": True}
        return {**marker, "state": state}
    if phase == "failed":
        state = {
            "pr": None,
            "label": "deploy failed · " + (marker.get("error") or "the push failed")[:160],
            "tone": "bad",
            "busy": False,
        }
        return {**marker, "state": state}
    running = marker["digest"] == box_digest
    state = await deploystatus.status(settings, marker["digest"], running)
    if state is None and running:
        # GitHub did not answer (or has no pull request for it), but the box already runs
        # this version: the deployment is over, whatever GitHub says later.
        state = {
            "pr": None,
            "label": "deployed · running in the box",
            "tone": "ok",
            "busy": False,
            "box_matches": True,
        }
    elif state is None:
        # Unknown. Hold other deployments only while this one can still be in flight; a
        # release takes minutes, so after half an hour GitHub's silence must not keep
        # every Deploy button grey.
        young = time.time() - float(marker.get("at") or 0) < 30 * 60
        state = {
            "pr": None,
            "label": "deploying · waiting for GitHub to show the pull request"
            if young
            else "deploy state unknown · GitHub did not answer",
            "tone": "now" if young else "bad",
            "busy": young,
            "box_matches": False,
        }
    elif state["pr"]["state"] == "closed":
        return None
    return {**marker, "state": state}


def _ui_keys() -> dict[str, str | None]:
    """The party keys this UI verifies signatures against (its trust file)."""
    try:
        trust = json.loads(settings.trust_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        trust = {}
    return {party: trust.get(f"{party}_key") for party in PARTIES}


async def _keys(box: dict) -> dict:
    """Each party's key here, on production and in the box, with a verdict."""
    manifest = box.get("manifest") or {}
    return await deploystatus.keys(settings, _ui_keys(), manifest.get("parties"))


@app.get("/agreements/new")
async def new_agreement_form(request: Request) -> Response:
    if user_of(request) is None:
        return to_login(request)
    view, error = load_view(settings)
    return render(request, "new_agreement.html", view=view, error=error, form={})


@app.post("/agreements/new")
async def new_agreement(request: Request) -> Response:
    if user_of(request) is None:
        return to_login(request)
    form = {k: str(v) for k, v in (await request.form()).items()}
    if form.get("chat") == "1" and not settings.anthropic_api_key:
        view, error = load_view(settings)
        return render(
            request,
            "new_agreement.html",
            view=view,
            error=error,
            form=form,
            err="AI creation is unavailable on this server. Create a draft manually instead.",
        )
    errors, created = pv.new_agreement(settings, form, user_of(request) or {})
    if errors or created is None:
        view, error = load_view(settings)
        return render(
            request, "new_agreement.html", view=view, error=error, form=form, err="; ".join(errors)
        )
    saved(user_of(request))
    chat = "&chat=1" if form.get("chat") == "1" else ""
    return back(
        f"/a/{created}/plan?edit=1{chat}", msg=f"New agreement {created}. Code and prompt copied."
    )


def _pins_differ(pre: dict, box: dict) -> bool:
    pins, live = (pre.get("release") or {}).get("image_digest"), box.get("image_digest")
    return bool(pins and live and pins != live)


async def _run_preflight(run_settings: Settings) -> dict:
    """The run's preconditions, plus one the files alone cannot tell: the release pins
    must be the release the box runs, or the attestation step fails with the SDK's
    catch-all "model_unavailable". ``run_settings`` is the fresh production snapshot
    when the server uses one (deployed.refresh); otherwise the checkout, which is pulled
    once here when the pins and the box disagree."""
    pre = runs.preflight(run_settings)
    box = await box_status(run_settings.box_url, pre.get("plan_digest") or "")
    if _pins_differ(pre, box) and run_settings is settings:
        if await asyncio.to_thread(publish.pull_now, settings.git_dir):
            pre = runs.preflight(settings)
    if _pins_differ(pre, box):
        tag = (pre.get("release") or {}).get("tag", "?")
        pre["missing"].append(
            f"the release pins here ({tag}) are not the release the box runs. A release may"
            " still be finishing, or this server has not caught up with production yet;"
            " try again in a minute. Until they agree the box cannot be attested."
        )
        pre["ok"] = False
    return pre


@app.get("/runs")
async def runs_index(request: Request) -> Response:
    if user_of(request) is None:
        return to_login(request)
    try:
        run_settings = await asyncio.to_thread(deployed.refresh, settings)
    except (OSError, subprocess.SubprocessError):
        return back(
            "/agreements",
            err="Could not fetch the deployed release. Check GitHub access and retry.",
        )
    pre = await _run_preflight(run_settings)
    qp = request.query_params
    win = {"from": qp.get("from", ""), "to": qp.get("to", ""), "who": qp.get("who", "")}
    lo, hi = _local_ts(win["from"]), _local_ts(win["to"])
    all_sessions = proxylogs.sessions(proxylogs.load_records(settings))
    whos = sorted({s.who for s in all_sessions})
    sessions = []
    for s in all_sessions:
        times = [r.time for r in s.requests]
        if (
            (win["who"] and s.who != win["who"])
            or (lo and max(times) < lo)
            or (hi and min(times) > hi)
        ):
            continue
        recs = proxylogs.to_box_records(s.requests)
        sessions.append(
            {
                "session": s,
                "notes": runs.scope_notes(run_settings, s.who, min(times), max(times)),
                "records": len(recs),
                "kb": round(len(json.dumps(recs).encode("utf-8")) / 1024, 1),
            }
        )
    totals = {
        "records": sum(i["records"] for i in sessions),
        "kb": round(sum(i["kb"] for i in sessions), 1),
        "refused": sum(1 for i in sessions if i["notes"]),
    }
    return render(
        request,
        "runs.html",
        pre=pre,
        sessions=sessions,
        all_count=len(all_sessions),
        whos=whos,
        win=win,
        totals=totals,
        source=qp.get("source", ""),
        picked=set(qp.getlist("session")),
        scope=runs.plan_scope(run_settings),
        runs=runs.list_runs(settings),
        plan_digest=pre["plan_digest"],
        plan_error=next((m for m in pre["missing"] if "deployed plan" in m), None),
    )


def _local_ts(value: str) -> float | None:
    """A datetime-local field value ("2026-10-15T09:00") as a UTC timestamp, or None."""
    try:
        return datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp() if value else None
    except ValueError:
        return None


@app.post("/runs/start")
async def runs_start(request: Request) -> Response:
    user = user_of(request)
    if user is None:
        return to_login(request)
    try:
        run_settings = await asyncio.to_thread(deployed.refresh, settings)
    except (OSError, subprocess.SubprocessError):
        return back(
            "/agreements",
            err="Could not fetch the deployed release. Check GitHub access and retry.",
        )
    pre = await _run_preflight(run_settings)
    if not pre["ok"]:
        return back("/runs", err="Cannot start: " + "; ".join(pre["missing"]))
    form = await request.form()
    try:
        if True:  # the input is always recorded sessions; test transcripts are gone
            chosen = set(form.getlist("session"))
            picked = [
                s for s in proxylogs.sessions(proxylogs.load_records(settings)) if s.id in chosen
            ]
            if not picked:
                return back("/runs", err="Pick at least one recorded session.")
            records = proxylogs.to_box_records([r for s in picked for r in s.requests])
            if not records:
                return back("/runs", err="The chosen sessions hold no text to audit.")
            label = f"{len(picked)} recorded session(s), {len(records)} records"
            run_id = runs.start_run(run_settings, user, records=records, label=label)
    except (ValueError, OSError) as exc:
        return back("/runs", err=str(exc))
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.get("/runs/{run_id}")
async def run_page(request: Request, run_id: str) -> Response:
    if user_of(request) is None:
        return to_login(request)
    state = runs.load_run(settings, run_id)
    if state is None:
        return back("/runs", err="No such run.")
    return render(request, "run.html", run=state)


@app.get("/attest")
async def attest(request: Request) -> Response:
    """The box's details now live on the Agreements page."""
    return RedirectResponse("/agreements", status_code=303)


@app.get("/plan")
async def legacy_plan() -> Response:
    return RedirectResponse("/a/plan/plan", status_code=303)


# --------------------------------------------------------------------------- one agreement


@app.get("/a/{aid}/plan")
async def plan(request: Request, aid: str) -> Response:
    target, redirect = agreement(request, aid)
    if redirect:
        return redirect
    view, error = load_view(target)
    q = request.query_params
    edit, editor = _edit_mode(request, aid, q.get("edit") == "1")
    # The change log can get long: filter by party or kind, show ten at a time.
    who, kind, show_all = q.get("who", ""), q.get("kind", ""), q.get("changes") == "all"
    history = view.history if view else []
    if who in PARTIES:
        history = [e for e in history if e.get("party") == who]
    if kind in CHANGE_KINDS:
        history = [e for e in history if e.get("kind") in CHANGE_KINDS[kind]]
    changes = {
        "entries": history if show_all else history[:10],
        "total": len(history),
        "who": who,
        "kind": kind,
        "all": show_all,
        "open": bool(who or kind or show_all),
        "kinds": list(CHANGE_KINDS),
    }
    return render(
        request,
        "plan.html",
        editor=editor,
        view=view,
        error=error,
        edit=edit,
        base=f"/a/{aid}",
        aid=aid,
        changes=changes,
        chat_enabled=bool(settings.anthropic_api_key),
        chat_initial=(
            "Create a plan for this question: " + view.config["classification"]["problem"]
            if view and q.get("chat") == "1"
            else ""
        ),
    )


@app.post("/a/{aid}/plan/chat")
@app.post("/a/{aid}/plan/chat/apply")
async def plan_chat_action(request: Request, aid: str) -> Response:
    actor = user_of(request)
    if actor is None:
        return JSONResponse({"error": "Please log in again."}, status_code=401)
    target = pv.for_agreement(settings, aid)
    if target is None:
        return JSONResponse({"error": "No such agreement."}, status_code=404)
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host"):
        return JSONResponse({"error": "Use chat from this site's plan editor."}, status_code=403)
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
        return JSONResponse({"error": "Expected a JSON request."}, status_code=415)
    try:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > plan_chat.MAX_BODY:
                raise plan_chat.ChatError("Chat request is too large.", 413)
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise plan_chat.ChatError("Expected a JSON object.")
        if request.url.path.endswith("/apply"):
            if aid == "plan":
                raise plan_chat.ChatError(READ_ONLY, 403)
            if other := editing.blocked_by(aid, actor):
                raise plan_chat.ChatError(editing.describe(other) + ".", 409)
            async with _lock(aid):
                digest = plan_chat.apply(target, payload, actor)
            result = {"message": "Changes saved as a draft.", "digest": digest}
            try:
                saved(actor)
            except (OSError, RuntimeError):
                # The local transaction succeeded; a background push failure must not
                # report a failed Apply and invite retrying a now-stale proposal.
                result["warning"] = (
                    "Draft saved, but its automatic Git push could not be scheduled."
                )
            return JSONResponse(result)
        return JSONResponse(
            await plan_chat.reply(
                target,
                payload.get("messages"),
                payload.get("candidate"),
                payload.get("events"),
            )
        )
    except plan_chat.ChatError as exc:
        return JSONResponse({"error": str(exc)}, status_code=exc.status)
    except (ValueError, UnicodeError):
        return JSONResponse({"error": "Could not read the request or plan."}, status_code=422)
    except OSError:
        return JSONResponse({"error": "Could not read or save the plan files."}, status_code=500)


@app.post("/a/{aid}/plan/edit")
async def start_editing(request: Request, aid: str) -> Response:
    """Entering edit mode takes the agreement for this person and removes existing
    signatures: the next save is a new version."""
    target, redirect = agreement(request, aid, write=True)
    if redirect:
        return redirect
    if busy := await _in_flight(aid):
        return back(f"/a/{aid}/plan", err=busy)
    other = editing.acquire(aid, user_of(request) or {})
    if other:
        return back(f"/a/{aid}/plan", err=editing.describe(other) + ". Try again later.")
    removed = pv.delete_signatures(target, user_of(request))
    saved(user_of(request))
    note = f" {removed} signature file(s) removed." if removed else ""
    form = await request.form()
    where = str(form.get("next", ""))
    if not where.startswith(f"/a/{aid}/plan"):
        where = f"/a/{aid}/plan"
    return back(f"{where}?edit=1", msg="Editing. Every save makes a new version." + note)


@app.post("/a/{aid}/plan/finish")
async def finish_editing(request: Request, aid: str) -> Response:
    """Give the agreement back so someone else can edit it."""
    _, redirect = agreement(request, aid, write=True)
    if redirect:
        return redirect
    editing.release(aid, user_of(request) or {})
    return back(f"/a/{aid}/plan", msg="Finished editing.")


@app.post("/a/{aid}/deploy")
async def deploy(request: Request, aid: str) -> Response:
    """Select for deployment: copy into plan/, push a plan-only branch, open the pull request."""
    target, redirect = agreement(request, aid, write=True)
    if redirect:
        return redirect
    actor = user_of(request) or {}
    view, error = load_view(target)
    if view is None:
        return back("/agreements", err=f"{aid} does not load: {error}. Fix it first.")
    if view.issue:
        return back(
            "/agreements",
            err=f"The box would reject {aid}: {view.issue} ({view.issue_where}). Fix it first.",
        )
    if not view.signed_by_both:
        return back(
            "/agreements",
            err=f"{aid} is not signed by both parties, so the signature check on the pull"
            " request would reject it. Sign it first, then deploy.",
        )
    box = await box_status(settings.box_url, "")
    keys = await _keys(box)
    if not keys["ready"]:
        why = "; ".join(f"{p}: {i['label']}" for p, i in keys["parties"].items())
        return back(
            "/agreements",
            err="The signatures would not verify on production, because the keys are not"
            f" trusted there yet ({why}). Submit the keys on the Sign screen and wait.",
        )
    current = await _current_deployment(box)
    if (
        current
        and current["state"]
        and current["state"]["busy"]
        and current["digest"] != view.digest
    ):
        return back(
            "/agreements",
            err=f"{current['name']} is being deployed right now ({current['state']['label']})."
            " Wait for it to finish before deploying another plan.",
        )
    if editing.holder(aid):
        return back("/agreements", err=editing.describe(editing.holder(aid)) + ". Deploy later.")
    pv.record_change(target, actor, "deploy", "deployment", "pushed for deployment")
    digest = view.digest
    # The push takes seconds to a minute (fetch, branch, push, pull request). It runs behind
    # the page: the page answers at once and its deployment strip follows the marker file.
    _remember_deploy(aid, view.plan_id, digest, phase="pushing")
    agreement_dir, plan_id = target.plan_dir, view.plan_id

    def push() -> None:
        pushed = publish.push_deploy(
            settings.git_dir, agreement_dir, aid, digest, actor, base=settings.deploy_base
        )
        if pushed.error:
            _remember_deploy(aid, plan_id, digest, phase="failed", error=pushed.error)
        else:
            _remember_deploy(
                aid, plan_id, digest, phase="pushed", pr_url=pushed.pr_url, branch=pushed.branch
            )

    threading.Thread(target=push, name=f"deploy-{aid}", daemon=True).start()
    saved(actor)  # the deploy is recorded in the agreement's change log
    # Redirect, so that a refresh re-reads the page instead of re-sending the form.
    return back(
        "/agreements",
        msg=f"{plan_id} is being deployed: the branch is pushed and the pull request opened,"
        " then the check merges it and the release follows. The deployment line follows it.",
    )


@app.post("/a/{aid}/delete")
async def delete_agreement(request: Request, aid: str) -> Response:
    target, redirect = agreement(request, aid, write=True)
    if redirect:
        return redirect
    if busy := await _in_flight(aid):
        return back("/agreements", err=busy)
    problem = pv.delete_agreement(settings, aid)
    if problem:
        return back("/agreements", err=problem)
    saved(user_of(request))
    return back("/agreements", msg=f"{aid} deleted.")


@app.get("/a/{aid}/plan/step/{name}")
async def plan_step(request: Request, aid: str, name: str) -> Response:
    target, redirect = agreement(request, aid)
    if redirect:
        return redirect
    view, error = load_view(target)
    step = view.step(name) if view else None
    if view and step is None:
        return back(f"/a/{aid}/plan", err=f"No step named {name}")
    edit, editor = _edit_mode(request, aid, request.query_params.get("edit") == "1")
    return render(
        request,
        "step.html",
        view=view,
        error=error,
        step=step,
        edit=edit,
        editor=editor,
        base=f"/a/{aid}",
        aid=aid,
    )


def _edit_mode(request: Request, aid: str, wanted: bool) -> tuple[bool, dict | None]:
    """Edit mode belongs to the lease holder. Returns (edit, the other holder or None)."""
    if aid == "plan":
        return False, None
    actor = user_of(request) or {}
    other = editing.blocked_by(aid, actor)
    if other:
        return False, other
    if wanted and editing.acquire(aid, actor):
        return False, editing.holder(aid)
    return wanted, None


@app.post("/a/{aid}/plan/step/{name}/settings")
async def save_settings(request: Request, aid: str, name: str) -> Response:
    target, redirect = agreement(request, aid, write=True)
    if redirect:
        return redirect
    async with _lock(aid):
        view, error = load_view(target)
        here = f"/a/{aid}/plan/step/{name}?edit=1"
        if view is None:
            return back(f"/a/{aid}/plan", err=error or "plan does not load")
        if view.frozen:
            return back(here, err=FROZEN)
        form = {k: str(v) for k, v in (await request.form()).items()}
        if held := _held(aid, user_of(request) or {}):
            return back(here, err=held)
        if stale := _stale(view, form.pop("expect", "")):
            return back(here, err=stale)
        errors = pv.save_step_settings(target, name, form, user_of(request) or {})
        if errors:
            return back(here, err="; ".join(errors))
        saved(user_of(request))
        return back(here, msg="Settings saved. This is a new version.")


@app.post("/a/{aid}/plan/step/{name}/code")
async def save_code(
    request: Request, aid: str, name: str, source: str = Form(...), expect: str = Form("")
) -> Response:
    target, redirect = agreement(request, aid, write=True)
    if redirect:
        return redirect
    async with _lock(aid):
        view, error = load_view(target)
        here = f"/a/{aid}/plan/step/{name}?edit=1"
        if view is None:
            return back(f"/a/{aid}/plan", err=error or "plan does not load")
        if view.frozen:
            return back(here, err=FROZEN)
        if held := _held(aid, user_of(request) or {}):
            return back(here, err=held)
        if stale := _stale(view, expect):
            return back(here, err=stale)
        problem = pv.save_function(target, name, source, user_of(request) or {})
        if problem:
            return back(here, err=problem)
        saved(user_of(request))
        return back(here, msg=f"{name}() saved. This is a new version.")


@app.post("/a/{aid}/plan/prompt/{prompt_id}")
async def save_prompt(
    request: Request,
    aid: str,
    prompt_id: str,
    text: str = Form(...),
    step: str = Form("classify"),
    expect: str = Form(""),
) -> Response:
    target, redirect = agreement(request, aid, write=True)
    if redirect:
        return redirect
    async with _lock(aid):
        view, error = load_view(target)
        here = f"/a/{aid}/plan/step/{step}?edit=1"
        if view is None:
            return back(f"/a/{aid}/plan", err=error or "plan does not load")
        if view.frozen:
            return back(here, err=FROZEN)
        if held := _held(aid, user_of(request) or {}):
            return back(here, err=held)
        if stale := _stale(view, expect):
            return back(here, err=stale)
        problem = pv.save_prompt(target, prompt_id, text, user_of(request) or {})
        if problem:
            return back(here, err=problem)
        saved(user_of(request))
        return back(here, msg="Prompt saved. This is a new version.")


@app.get("/a/{aid}/plan/bundle.zip")
async def plan_bundle(request: Request, aid: str) -> Response:
    """The exact files the digest covers, so a party can recompute it independently."""
    target, redirect = agreement(request, aid)
    if redirect:
        return redirect
    import io
    import zipfile

    from aiab.baked.bundle import bundle_files

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(bundle_files(target.plan_dir).items()):
            archive.writestr(name, raw)
    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="plan.zip"'},
    )


@app.get("/a/{aid}/sign")
async def sign(request: Request, aid: str) -> Response:
    target, redirect = agreement(request, aid, write=True)
    if redirect:
        return redirect
    view, error = load_view(target)
    keys = await _keys(await box_status(settings.box_url, ""))
    return render(
        request, "sign.html", view=view, error=error, base=f"/a/{aid}", aid=aid, keys=keys
    )


@app.post("/a/{aid}/sign/upload")
async def sign_upload(
    request: Request,
    aid: str,
    party: Annotated[str, Form()],
    signature: Annotated[str, Form()] = "",
) -> Response:
    # The default keeps an empty paste from being a 422; the browser sends "" for an empty box.
    target, redirect = agreement(request, aid, write=True)
    if redirect:
        return redirect
    user = user_of(request)
    here = f"/a/{aid}/sign"
    if busy := await _in_flight(aid):
        return back(here, err=busy)
    if user is None or party != user["party"]:
        return back(here, err="Paste only your own party's signature.")
    if not signature.strip():
        return back(here, err="Paste the line the sign command printed first.")
    status, detail = pv.write_signature(target, party, signature.encode("utf-8"), user)
    if status != "ok":
        return back(here, err=f"Signature not accepted: {detail}")
    saved(user)
    return back(here, msg=f"{party} signature verified and stored.")


@app.post("/a/{aid}/sign/remove")
async def sign_remove(request: Request, aid: str, party: Annotated[str, Form()]) -> Response:
    """Re-sign: remove your own signature for this version, then the paste form comes back."""
    target, redirect = agreement(request, aid, write=True)
    if redirect:
        return redirect
    user = user_of(request)
    here = f"/a/{aid}/sign"
    if busy := await _in_flight(aid):
        return back(here, err=busy)
    if user is None or party != user["party"]:
        return back(here, err="You can only remove your own signature.")
    if not pv.remove_signature(target, party, user):
        return back(here, err="There is no signature of yours to remove.")
    saved(user)
    return back(here, msg="Your signature is removed. Sign this version again below.")


@app.post("/a/{aid}/sign/key")
async def set_key(
    request: Request,
    aid: str,
    party: Annotated[str, Form()],
    public_key: Annotated[str, Form()] = "",
) -> Response:
    target, redirect = agreement(request, aid, write=True)
    if redirect:
        return redirect
    user = user_of(request)
    here = f"/a/{aid}/sign"
    if user is None or party != user["party"]:
        return back(here, err="You can only replace your own party's public key.")
    public_key = public_key.strip()
    if problem := pv.check_public_key(public_key):
        return back(here, err=problem)
    if pv.load_trust(settings).get(f"{party}_key") == public_key:
        return back(here, msg=f"Production already trusts this {party} key. Nothing to submit.")
    pushed = publish.push_key_change(
        settings.git_dir, party, public_key, user, base=settings.deploy_base
    )
    if pushed.error:
        return back(here, err=f"The {party} key was not submitted: {pushed.error}")
    pv.record_change(target, user, "key", f"public key: {party}", f"submitted {public_key}")
    where = pushed.pr_url or pushed.compare_url or f"branch {pushed.branch}"
    return back(
        here,
        msg=f"The {party} key is submitted ({where}). The check merges it within a minute and"
        " production trusts it then; the status line follows it. Sign once it says trusted.",
    )
