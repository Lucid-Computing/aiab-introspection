"""The agreement UI: a FastAPI front end over the plan folder and the box's read-only endpoints."""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import quote

from aiab.plan_bundle import PARTIES
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from server import plan_view as pv
from server.auth import SessionSigner, authenticate
from server.box import box_status
from server.config import UI_ROOT, load_settings

settings = load_settings()
signer = SessionSigner(settings.session_secret)
app = FastAPI(title="AIAB agreement UI", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/assets", StaticFiles(directory=UI_ROOT / "assets"), name="assets")
templates = Jinja2Templates(directory=UI_ROOT / "server" / "templates")
COOKIE = "aiab_ui"

FLOW: list[tuple[str, str]] = [
    ("New agreement", "A branch and a draft pull request for the next plan version."),
    ("Edit", "Each save is one commit by the party who made it. Validation runs on every save."),
    ("Discuss", "On the pull request page, until comments live here."),
    (
        "Dry run and record",
        "Run the plan over the synthetic transcripts; record FP/FN into plan.json.",
    ),
    ("Freeze", "No more edits. The plan digest is final."),
    ("Sign", "Each party signs the digest on its own machine and uploads the signature file."),
    ("Check", "CI recomputes the digest, verifies both signatures and the restrictions."),
    ("Merge and tag", "Auto-merge on green; the tag starts the image build."),
    ("Build", "Reproducible build; release with image digest and plan digest."),
    ("Deploy", "The prover's operator deploys the tag to the Tinfoil CVM."),
    ("Attest", "Release, manifest and attestation agree: VERIFIED."),
    ("Runs", "Submit runs, read signed reports."),
]


def flow_index(view: pv.PlanView, box: dict[str, Any]) -> int:
    if box.get("plan_matches"):
        return 10
    if view.signed_by_both:
        return 6
    if view.frozen:
        return 5
    if view.errors:
        return 1
    if view.evaluation_current:
        return 4
    return 3


def user_of(request: Request) -> dict[str, str] | None:
    return signer.read(request.cookies.get(COOKIE))


def to_login(request: Request) -> RedirectResponse:
    return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)


def back(path: str, *, msg: str | None = None, err: str | None = None) -> RedirectResponse:
    if msg:
        path += f"?msg={quote(msg)}"
    elif err:
        path += f"?err={quote(err)}"
    return RedirectResponse(path, status_code=303)


def render(request: Request, template: str, **context: Any) -> Response:
    context.update(
        user=user_of(request),
        settings=settings,
        active=template.removesuffix(".html"),
        msg=request.query_params.get("msg"),
        err=request.query_params.get("err"),
        parties=PARTIES,
    )
    return templates.TemplateResponse(request, template, context)


def load_view() -> tuple[pv.PlanView | None, str | None]:
    try:
        return pv.load_plan_view(settings), None
    except Exception as exc:  # noqa: BLE001 - show the broken plan instead of a 500
        return None, f"{type(exc).__name__}: {exc}"


FROZEN = "The plan is frozen. Unfreeze on the Sign screen to edit."


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


# --------------------------------------------------------------------------- screens


@app.get("/")
async def index(request: Request) -> Response:
    if user_of(request) is None:
        return to_login(request)
    return RedirectResponse("/how", status_code=303)


@app.get("/how")
async def how(request: Request) -> Response:
    if user_of(request) is None:
        return to_login(request)
    view, error = load_view()
    box = await box_status(settings.box_url, view.bundle.digest if view else "")
    current = flow_index(view, box) if view else 1
    return render(request, "how.html", view=view, error=error, flow=FLOW, current=current, box=box)


@app.get("/agreements")
async def agreements(request: Request) -> Response:
    if user_of(request) is None:
        return to_login(request)
    view, error = load_view()
    box = await box_status(settings.box_url, view.bundle.digest if view else "")
    return render(request, "agreements.html", view=view, error=error, box=box)


@app.get("/plan")
async def plan(request: Request) -> Response:
    if user_of(request) is None:
        return to_login(request)
    view, error = load_view()
    return render(request, "plan.html", view=view, error=error)


@app.get("/plan/step/{name}")
async def plan_step(request: Request, name: str) -> Response:
    if user_of(request) is None:
        return to_login(request)
    view, error = load_view()
    step = view.step(name) if view else None
    if view and step is None:
        return back("/plan", err=f"No step named {name}")
    return render(request, "step.html", view=view, error=error, step=step)


@app.post("/plan/step/{name}/settings")
async def save_settings(request: Request, name: str) -> Response:
    if user_of(request) is None:
        return to_login(request)
    view, error = load_view()
    if view is None:
        return back("/plan", err=error or "plan does not load")
    if view.frozen:
        return back(f"/plan/step/{name}", err=FROZEN)
    form = {k: str(v) for k, v in (await request.form()).items()}
    errors = pv.save_step_settings(settings, name, form)
    if errors:
        return back(f"/plan/step/{name}", err="; ".join(errors))
    return back(f"/plan/step/{name}", msg="Settings saved to plan.json. The plan digest changed.")


@app.post("/plan/step/{name}/code")
async def save_code(request: Request, name: str, source: str = Form(...)) -> Response:
    if user_of(request) is None:
        return to_login(request)
    view, error = load_view()
    if view is None:
        return back("/plan", err=error or "plan does not load")
    if view.frozen:
        return back(f"/plan/step/{name}", err=FROZEN)
    problem = pv.save_function(settings, name, source)
    if problem:
        return back(f"/plan/step/{name}", err=problem)
    return back(f"/plan/step/{name}", msg=f"{name}() saved to plan.py. The plan digest changed.")


@app.post("/plan/prompt/{prompt_id}")
async def save_prompt(
    request: Request, prompt_id: str, text: str = Form(...), step: str = Form("classify")
) -> Response:
    if user_of(request) is None:
        return to_login(request)
    view, error = load_view()
    if view is None:
        return back("/plan", err=error or "plan does not load")
    if view.frozen:
        return back(f"/plan/step/{step}", err=FROZEN)
    problem = pv.save_prompt(settings, prompt_id, text)
    if problem:
        return back(f"/plan/step/{step}", err=problem)
    return back(
        f"/plan/step/{step}", msg=f"prompts/{prompt_id}.txt saved. The plan digest changed."
    )


@app.get("/dryrun")
async def dryrun(request: Request) -> Response:
    if user_of(request) is None:
        return to_login(request)
    view, error = load_view()
    return render(request, "dryrun.html", view=view, error=error, result=None)


@app.post("/dryrun")
async def dryrun_run(request: Request, action: str = Form("run")) -> Response:
    if user_of(request) is None:
        return to_login(request)
    view, error = load_view()
    if view is None:
        return back("/dryrun", err=error or "plan does not load")
    if action == "record" and view.frozen:
        return back("/dryrun", err="The plan is frozen; recording would change plan.json.")
    result = pv.run_dry_subprocess(settings, record=(action == "record"))
    view, error = load_view()  # recording changes plan.json and therefore the digest
    return render(
        request,
        "dryrun.html",
        view=view,
        error=error,
        result=result,
        recorded=(action == "record"),
    )


@app.get("/sign")
async def sign(request: Request) -> Response:
    if user_of(request) is None:
        return to_login(request)
    view, error = load_view()
    return render(request, "sign.html", view=view, error=error)


@app.post("/sign/upload")
async def sign_upload(
    request: Request, party: Annotated[str, Form()], file: Annotated[UploadFile, File()]
) -> Response:
    user = user_of(request)
    if user is None:
        return to_login(request)
    if party != user["party"]:
        return back("/sign", err=f"You are the {user['party']}; upload only your own signature.")
    content = await file.read()
    if len(content) > 8192:
        return back("/sign", err="Signature file is too large.")
    status, detail = pv.write_signature(settings, party, content)
    if status != "ok":
        return back("/sign", err=f"Signature rejected ({status}): {detail}")
    return back("/sign", msg=f"{party} signature verified and stored in plan/signatures/.")


@app.post("/sign/unfreeze")
async def unfreeze(request: Request) -> Response:
    if user_of(request) is None:
        return to_login(request)
    removed = pv.delete_signatures(settings)
    return back(
        "/sign", msg=f"Unfrozen: {removed} signature file(s) removed. Both parties sign again."
    )


@app.get("/attest")
async def attest(request: Request) -> Response:
    if user_of(request) is None:
        return to_login(request)
    view, error = load_view()
    box = await box_status(settings.box_url, view.bundle.digest if view else "")
    return render(request, "attest.html", view=view, error=error, box=box)
