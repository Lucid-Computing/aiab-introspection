"""The agreement UI: a FastAPI front end over the plan folders and the box's read-only endpoints."""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from server import plan_view as pv
from server import publish, runs
from server.auth import SessionSigner, authenticate
from server.box import box_status
from server.config import UI_ROOT, Settings, load_settings
from server.pipeline import PARTIES

settings = load_settings()
signer = SessionSigner(settings.session_secret)
app = FastAPI(title="AIAB agreement UI", docs_url=None, redoc_url=None, openapi_url=None)
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
            "Open it to see the five steps the box runs, in order: filter which records count, "
            "group them into queries, classify each query, turn scores into a verdict, "
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


def load_view(target: Settings) -> tuple[pv.PlanView | None, str | None]:
    try:
        return pv.load_plan_view(target), None
    except Exception as exc:  # noqa: BLE001 - show the broken plan instead of a 500
        return None, f"{type(exc).__name__}: {exc}"


def agreement(request: Request, aid: str) -> tuple[Settings | None, Response | None]:
    """Resolve an agreement id, or the redirect to send instead."""
    if user_of(request) is None:
        return None, to_login(request)
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


@app.get("/agreements")
async def agreements(request: Request) -> Response:
    if user_of(request) is None:
        return to_login(request)
    rows = pv.list_agreements(settings)
    selected = next((r for r in rows if r["id"] == "plan"), None)
    box = await box_status(settings.box_url, selected["digest"] if selected else "")
    return render(request, "agreements.html", rows=rows, box=box)


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
    errors, created = pv.new_agreement(settings, form, user_of(request) or {})
    if errors or created is None:
        view, error = load_view(settings)
        return render(
            request, "new_agreement.html", view=view, error=error, form=form, err="; ".join(errors)
        )
    saved(user_of(request))
    return back(
        f"/a/{created}/plan?edit=1", msg=f"New agreement {created}. Code and prompt copied."
    )


@app.get("/runs")
async def runs_index(request: Request) -> Response:
    if user_of(request) is None:
        return to_login(request)
    view, _ = load_view(settings)
    return render(
        request,
        "runs.html",
        pre=runs.preflight(settings),
        transcripts=runs.transcripts(settings),
        runs=runs.list_runs(settings),
        plan_digest=view.digest if view else None,
    )


@app.post("/runs/start")
async def runs_start(request: Request, transcript: Annotated[str, Form()] = "") -> Response:
    user = user_of(request)
    if user is None:
        return to_login(request)
    pre = runs.preflight(settings)
    if not pre["ok"]:
        return back("/runs", err="Cannot start: " + "; ".join(pre["missing"]))
    try:
        run_id = runs.start_run(settings, transcript, user)
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
    if user_of(request) is None:
        return to_login(request)
    view, error = load_view(settings)
    box = await box_status(settings.box_url, view.digest if view else "")
    return render(request, "attest.html", view=view, error=error, box=box)


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
    edit = q.get("edit") == "1"
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
        view=view,
        error=error,
        edit=edit,
        base=f"/a/{aid}",
        aid=aid,
        changes=changes,
    )


@app.post("/a/{aid}/plan/edit")
async def start_editing(request: Request, aid: str) -> Response:
    """Entering edit mode removes existing signatures: the next save is a new version."""
    target, redirect = agreement(request, aid)
    if redirect:
        return redirect
    removed = pv.delete_signatures(target, user_of(request))
    saved(user_of(request))
    note = f" {removed} signature file(s) removed." if removed else ""
    form = await request.form()
    where = str(form.get("next", ""))
    if not where.startswith(f"/a/{aid}/plan"):
        where = f"/a/{aid}/plan"
    return back(f"{where}?edit=1", msg="Editing. Every save makes a new version." + note)


@app.post("/a/{aid}/deploy")
async def deploy(request: Request, aid: str) -> Response:
    """Select for deployment: copy into plan/, push a plan-only branch, open the pull request."""
    target, redirect = agreement(request, aid)
    if redirect:
        return redirect
    actor = user_of(request) or {}
    problem = pv.deploy_agreement(settings, aid, actor)
    if problem:
        return back("/agreements", err=problem)
    view, _ = load_view(target)
    digest = view.digest if view else ""
    pushed = publish.push_deploy(
        settings.git_dir, target.plan_dir, aid, digest, actor, base=settings.deploy_base
    )
    saved(actor)  # the deploy is recorded in the agreement's change log
    rows = pv.list_agreements(settings)
    selected = next((r for r in rows if r["id"] == "plan"), None)
    box = await box_status(settings.box_url, selected["digest"] if selected else "")
    return render(request, "agreements.html", rows=rows, box=box, pushed=pushed, deployed=aid)


@app.post("/a/{aid}/delete")
async def delete_agreement(request: Request, aid: str) -> Response:
    target, redirect = agreement(request, aid)
    if redirect:
        return redirect
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
    edit = request.query_params.get("edit") == "1"
    return render(
        request,
        "step.html",
        view=view,
        error=error,
        step=step,
        edit=edit,
        base=f"/a/{aid}",
        aid=aid,
    )


@app.post("/a/{aid}/plan/step/{name}/settings")
async def save_settings(request: Request, aid: str, name: str) -> Response:
    target, redirect = agreement(request, aid)
    if redirect:
        return redirect
    view, error = load_view(target)
    here = f"/a/{aid}/plan/step/{name}?edit=1"
    if view is None:
        return back(f"/a/{aid}/plan", err=error or "plan does not load")
    if view.frozen:
        return back(here, err=FROZEN)
    form = {k: str(v) for k, v in (await request.form()).items()}
    errors = pv.save_step_settings(target, name, form, user_of(request) or {})
    if errors:
        return back(here, err="; ".join(errors))
    saved(user_of(request))
    return back(here, msg="Settings saved. This is a new version.")


@app.post("/a/{aid}/plan/step/{name}/code")
async def save_code(request: Request, aid: str, name: str, source: str = Form(...)) -> Response:
    target, redirect = agreement(request, aid)
    if redirect:
        return redirect
    view, error = load_view(target)
    here = f"/a/{aid}/plan/step/{name}?edit=1"
    if view is None:
        return back(f"/a/{aid}/plan", err=error or "plan does not load")
    if view.frozen:
        return back(here, err=FROZEN)
    problem = pv.save_function(target, name, source, user_of(request) or {})
    if problem:
        return back(here, err=problem)
    saved(user_of(request))
    return back(here, msg=f"{name}() saved. This is a new version.")


@app.post("/a/{aid}/plan/prompt/{prompt_id}")
async def save_prompt(
    request: Request, aid: str, prompt_id: str, text: str = Form(...), step: str = Form("classify")
) -> Response:
    target, redirect = agreement(request, aid)
    if redirect:
        return redirect
    view, error = load_view(target)
    here = f"/a/{aid}/plan/step/{step}?edit=1"
    if view is None:
        return back(f"/a/{aid}/plan", err=error or "plan does not load")
    if view.frozen:
        return back(here, err=FROZEN)
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
    target, redirect = agreement(request, aid)
    if redirect:
        return redirect
    view, error = load_view(target)
    return render(request, "sign.html", view=view, error=error, base=f"/a/{aid}", aid=aid)


@app.post("/a/{aid}/sign/upload")
async def sign_upload(
    request: Request,
    aid: str,
    party: Annotated[str, Form()],
    signature: Annotated[str, Form()] = "",
) -> Response:
    # The default keeps an empty paste from being a 422; the browser sends "" for an empty box.
    target, redirect = agreement(request, aid)
    if redirect:
        return redirect
    user = user_of(request)
    here = f"/a/{aid}/sign"
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
    target, redirect = agreement(request, aid)
    if redirect:
        return redirect
    user = user_of(request)
    here = f"/a/{aid}/sign"
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
    target, redirect = agreement(request, aid)
    if redirect:
        return redirect
    user = user_of(request)
    here = f"/a/{aid}/sign"
    if user is None or party != user["party"]:
        return back(here, err="You can only replace your own party's public key.")
    problem = pv.set_public_key(target, party, public_key, user)
    if problem:
        return back(here, err=problem)
    return back(here, msg=f"Public key for the {party} replaced. Sign with the matching key.")
