"""The agreement UI: a FastAPI front end over the plan folders and the box's read-only endpoints."""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from server import (
    deploystatus,
    editing,
    githubexport,
    plan_chat,
    plantlogs,
    proxylogs,
    publish,
    runs,
    slackexport,
)
from server import plan_view as pv
from server.auth import LoginError, OidcLogin, SessionSigner, Stamp, authenticate, pkce_pair
from server.box import box_status
from server.config import UI_ROOT, LogSource, Settings, load_settings
from server.plan_view import PARTIES

settings = load_settings()
signer = SessionSigner(settings.session_secret)
stamp = Stamp(settings.session_secret)  # the state of a login in flight, in its own cookie
# OpenID Connect (WorkOS, or any provider) when configured. The local accounts file works
# alongside it: demo and emergency accounts the operator makes on this side (`make account`).
oidc: OidcLogin | None = (
    OidcLogin(
        settings.oidc_issuer,  # type: ignore[arg-type]
        settings.oidc_client_id,  # type: ignore[arg-type]
        settings.oidc_client_secret,  # type: ignore[arg-type]
        admins_file=settings.admins_file,
        group=settings.oidc_group,
        groups_claim=settings.oidc_groups_claim,
        scopes=settings.oidc_scopes,
    )
    if settings.oidc_enabled
    else None
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        moved = pv.archive_old_agreements(settings)  # the box no longer runs that format
        if moved:
            print(f"archived {len(moved)} agreement(s) in the previous plan format: {moved}")
    except OSError as exc:
        print(f"warning: could not archive old agreements: {exc}")
    if oidc is None:
        print("warning: OIDC_ISSUER/OIDC_CLIENT_ID/OIDC_CLIENT_SECRET unset; password login only")
    try:
        moved = proxylogs.adopt_legacy_layout(settings)  # test segments now live in dev/
        if moved:
            print(f"moved {len(moved)} test proxy file(s) into the Dev Agent Logs folder")
    except OSError as exc:
        print(f"warning: could not move the test proxy's segments into dev/: {exc}")
    opened = slackexport.unpack_at_start(settings)  # the committed bundle, into logs/slack
    if opened:
        print(f"opened the Slack export bundle: {len(opened)} channel file(s)")
    opened = githubexport.unpack_at_start(settings)  # the same, into logs/github
    if opened:
        print(f"opened the GitHub export bundle: {len(opened)} repository file(s)")
    proxylogs.start_prefetch(settings)  # keeps the Logs page's copy of every proxy fresh
    asyncio.ensure_future(deploystatus.warm_up(settings))  # first Agreements load is fast too
    refresher = asyncio.ensure_future(deploystatus.refresh_loop())  # GitHub answers stay warm
    if settings.auto_pull:
        publish.start_auto_pull(settings.git_dir)  # the release pins follow the branch
    yield
    refresher.cancel()


app = FastAPI(
    title="AIAB agreement UI", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
)
app.mount("/assets", StaticFiles(directory=UI_ROOT / "assets"), name="assets")
templates = Jinja2Templates(directory=UI_ROOT / "server" / "templates")
COOKIE = "aiab_ui"
LOGIN_COOKIE = "aiab_login"  # state, nonce and PKCE verifier while the provider has the browser
ID_TOKEN_COOKIE = "aiab_idt"  # the provider's ID token, only so Log out can end its session too
LOGS_PAGE_SIZE = 50  # sessions per page on the Logs screen
LOGS_RECENT = 6  # messages of a call shown unfolded; earlier ones load when opened


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
    "plan": ("plan", "settings", "code", "prompt"),
    "signing": ("signature", "key"),
    "runs": ("run", "created"),
}
FROZEN = "A valid signature exists for this version. Use Edit on the Agreements screen first."
EDIT_WARNING = (
    "Editing makes a new version of the plan. Both parties must sign it again, "
    "and any existing signature is removed now. Continue?"
)


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
    context.setdefault("key_wait", _key_wait() if user_of(request) else None)
    # Sign buttons get the browser's key certified first when the server holds the party's
    # root (prototype).
    who = user_of(request)
    context.setdefault(
        "server_certifies", bool(who) and pv.server_certifies(settings, who["party"])
    )
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


def agreement(
    request: Request, aid: str, write: bool = False
) -> tuple[Settings | None, Response | None]:
    """Resolve an agreement id, or the redirect to send instead."""
    if user_of(request) is None:
        return None, to_login(request)
    target = pv.for_agreement(settings, aid)
    if target is None:
        return None, back("/agreements", err=f"No agreement called {aid}")
    return target, None


# --------------------------------------------------------------------------- auth


def public_origin(request: Request) -> str:
    """The address this UI is reached at, as the provider must see it in redirect URIs:
    X-Forwarded-Proto and Host behind Render's proxy, the request's own otherwise."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host}"


def safe_next(value: str | None) -> str:
    return value if value and value.startswith("/") and not value.startswith("//") else "/"


@app.get("/login")
async def login_form(request: Request) -> Response:
    return render(request, "login.html", next=safe_next(request.query_params.get("next")))


@app.get("/login/start")
async def login_start(request: Request) -> Response:
    """Send the browser to the provider. What must come back unchanged (state), what the ID
    token must carry (nonce) and the PKCE verifier travel in a short-lived signed cookie."""
    if oidc is None:
        return back("/login", err="OpenID Connect login is not configured on this server.")
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier, challenge = pkce_pair()
    redirect_uri = public_origin(request) + "/login/callback"
    try:
        url = await asyncio.to_thread(oidc.authorize_url, redirect_uri, state, nonce, challenge)
    except LoginError as exc:
        return render(request, "login.html", next="/", err=str(exc))
    response = RedirectResponse(url, status_code=303)
    sealed = stamp.seal(
        {"s": state, "n": nonce, "v": verifier, "next": safe_next(request.query_params.get("next"))}
    )
    response.set_cookie(LOGIN_COOKIE, sealed, httponly=True, samesite="lax", max_age=600)
    return response


@app.get("/login/callback")
async def login_callback(request: Request) -> Response:
    """The provider sent the browser back with a code: exchange it, check the ID token, map
    the groups to the party, and issue the UI's own cookie."""
    if oidc is None:
        return back("/login", err="OpenID Connect login is not configured on this server.")
    pending = stamp.open(request.cookies.get(LOGIN_COOKIE))
    qp = request.query_params
    if pending is None or not qp.get("state") or qp.get("state") != pending.get("s"):
        return render(
            request,
            "login.html",
            next="/",
            err="This login attempt is not the one this browser started, or it took longer"
            " than ten minutes. Sign in again.",
        )
    if qp.get("error"):
        detail = qp.get("error_description") or qp["error"]
        refused = f"{settings.oidc_provider_name} refused: {detail}"
        return render(request, "login.html", next=pending["next"], err=refused)
    if not qp.get("code"):
        return render(request, "login.html", next=pending["next"], err="No login code came back.")
    redirect_uri = public_origin(request) + "/login/callback"
    try:
        account, id_token = await asyncio.to_thread(
            oidc.login, qp["code"], redirect_uri, pending["v"], pending["n"]
        )
    except LoginError as exc:
        response = render(request, "login.html", next=pending["next"], err=str(exc))
        response.delete_cookie(LOGIN_COOKIE)
        return response
    response = RedirectResponse(pending["next"], status_code=303)
    response.delete_cookie(LOGIN_COOKIE)
    response.set_cookie(COOKIE, signer.issue(account), httponly=True, samesite="lax")
    response.set_cookie(ID_TOKEN_COOKIE, id_token, httponly=True, samesite="lax")
    return response


@app.post("/login")
async def login(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
) -> Response:
    """The local accounts' password form. Works with or without the provider: local accounts are
    the operator's own (demo users, a way in when the provider is down)."""
    target = safe_next(next)
    account = authenticate(settings.accounts_file, username.strip(), password)
    if account is None:
        return render(request, "login.html", next=target, err="Unknown account or wrong password.")
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(COOKIE, signer.issue(account), httponly=True, samesite="lax")
    return response


@app.post("/logout")
async def logout(request: Request) -> Response:
    """End the UI session, and the provider's too when it offers that, so that the next Sign
    in asks who you are instead of silently picking the same person again."""
    target = "/login"
    id_token = request.cookies.get(ID_TOKEN_COOKIE)
    if oidc is not None and id_token:
        url = await asyncio.to_thread(oidc.logout_url, id_token, public_origin(request) + "/login")
        if url:
            target = url
    response = RedirectResponse(target, status_code=303)
    response.delete_cookie(COOKIE)
    response.delete_cookie(ID_TOKEN_COOKIE)
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
    return render(request, "how.html")


# The audit plan playground (ui/playground): a standalone prototype, served from its build
# output when `pnpm build` has been run there. Static: it reads no agreement and calls no
# endpoint; plans live in the browser.
PLAYGROUND_DIR = UI_ROOT / "playground" / "dist"


@app.get("/audit-plan-playground")
@app.get("/audit-plan-playground/{path:path}")
async def playground(request: Request, path: str = "") -> Response:
    if user_of(request) is None:
        return to_login(request)
    root = PLAYGROUND_DIR.resolve()
    if not (root / "index.html").is_file():
        return Response(
            "The playground is not built: run `pnpm install && pnpm build` in ui/playground.",
            status_code=404,
            media_type="text/plain",
        )
    target = (root / (path or "index.html")).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return Response(status_code=404)
    return FileResponse(target)


def _run_source(request: Request, folder: Path | None, form: Any = None) -> tuple[LogSource, bool]:
    """The log source a run reads: the one the agreement's Input filter names, when it is
    one of this server's sources; otherwise the page's ``logs`` choice (plans written before
    the filter named a source). The flag says whether the agreement decided."""
    named = runs.plan_source(folder) if folder else None
    by_id = {s.id: s for s in settings.log_sources}
    if named in by_id:
        return by_id[named], True
    return _log_source(request, form), False


def _log_source(request: Request, form: Any = None) -> LogSource:
    """The log source a page works on, from its ``logs`` query parameter (or form field):
    ``prod`` for Prod Agent Logs, ``dev`` for Dev Agent Logs, the default."""
    wanted = (form or request.query_params).get("logs")
    return settings.log_source(str(wanted) if wanted else None)


@app.get("/logs/sessions.json")
async def logs_sessions(request: Request) -> Response:
    """The sessions of one log source, by metadata only, for the Input filter's picker."""
    if user_of(request) is None:
        return JSONResponse({"error": "sign in"}, status_code=401)
    source = _log_source(request)
    out = []
    for s in proxylogs.sessions(proxylogs.load_index(settings, source)):
        records = len(proxylogs.box_records(s.records))
        if records:
            out.append(
                {
                    "id": s.id,
                    "title": s.title,
                    "who": s.who,
                    "started": s.started_short,
                    "records": records,
                }
            )
    return JSONResponse({"source": source.id, "sessions": out})


@app.get("/logs")
async def logs_page(request: Request) -> Response:
    if user_of(request) is None:
        return to_login(request)
    source = _log_source(request)
    q = request.query_params.get("q", "").strip()
    who = request.query_params.get("who", "").strip()
    wanted = request.query_params.get("session", "")
    wanted_req = request.query_params.get("req", "")
    records = proxylogs.load_index(settings, source)
    all_sessions = proxylogs.sessions(records)
    whos = sorted({s.who for s in all_sessions})
    show_empty = request.query_params.get("empty") == "1"
    matched = [s for s in all_sessions if not who or s.who == who]
    # A session with nothing the box could read (no text in any record) is hidden by default.
    empty = sum(1 for s in matched if not proxylogs.box_records(s.records))
    if not show_empty and empty:
        matched = [s for s in matched if proxylogs.box_records(s.records)]
    if q:
        needle = q.lower()
        matched = [s for s in matched if needle in s.title.lower()]
    pages = max(1, -(-len(matched) // LOGS_PAGE_SIZE))
    try:
        page = min(max(1, int(request.query_params.get("page", "1"))), pages)
    except ValueError:
        page = 1
    shown = matched[(page - 1) * LOGS_PAGE_SIZE : page * LOGS_PAGE_SIZE]
    # One session's page only when it was asked for; an unknown id falls back to the list.
    current = next((s for s in all_sessions if s.id == wanted), None) if wanted else None
    req = None
    if current:
        picked = (
            next((r for r in current.records if r.id == wanted_req), None) or (current.records[-1])
        )
        # A proxied call is shown by its metadata only; its text is never read back here.
        # Slack and GitHub posts are public, and shown whole.
        req = (
            picked
            if isinstance(picked, proxylogs.Request)
            else proxylogs.load_full(settings, source, picked)
        )
    return render(
        request,
        "logs.html",
        source=source,
        sources=settings.log_sources,
        shown=shown,
        current=current,
        req=req,
        q=q,
        who=who,
        whos=whos,
        page=page,
        pages=pages,
        recent=LOGS_RECENT,
        matched=len(matched),
        empty=empty,
        show_empty=show_empty,
        total_requests=len(records),
        fetch=proxylogs.status(source),
        plant=(
            plantlogs.timeline(source.path(settings), current.records)
            if current and source.kind == "plant"
            else None
        ),
    )


@app.get("/logs/earlier/{rid}")
async def logs_earlier(request: Request, rid: str) -> Response:
    """Gone: a proxied call's messages are never served by this server (MAC-377). Kept so an
    old page's fetch gets a clear answer, not a route error."""
    if user_of(request) is None:
        return Response("not signed in", status_code=401)
    return Response("the text of a call is not served; it is for the box", status_code=404)


@app.get("/logs/raw/{rid}")
async def logs_raw(request: Request, rid: str) -> Response:
    """The recorded request as the proxy stored it, with the JSON bodies unpacked."""
    if user_of(request) is None:
        return to_login(request)
    source = _log_source(request)
    light = next((r for r in proxylogs.load_index(settings, source) if r.id == rid), None)
    if isinstance(light, proxylogs.Request):  # a proxied call's text is for the box only
        return Response("the text of a call is not served; it is for the box", status_code=404)
    entry = proxylogs.raw_entry(settings, source, light) if light else None
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
    source = _log_source(request, await request.form())
    here = f"/logs?logs={source.id}"
    if not source.fetches:
        return back(here, err=f"{source.label} are not fetched by this server.")
    try:
        result = proxylogs.sync_now(settings, source)
    except proxylogs.ProxyError as exc:
        return back(here, err=str(exc))
    return back(
        here,
        msg=f"{source.label}: fetched {result['new']} new segment(s); {result['segments']} on "
        f"disk, {result['live_entries']} live request(s) not sealed yet.",
    )


@app.get("/agreements")
async def agreements(request: Request) -> Response:
    if user_of(request) is None:
        return to_login(request)
    editing.release_all(user_of(request) or {})  # back on the list is leaving edit mode
    return await _agreements_page(request)


async def _agreements_page(request: Request, **extra) -> Response:
    """Agreements (editable, under agreements/), the box and the parties' keys."""
    box = await box_status(settings.box_url)
    rows = pv.list_agreements(settings)
    keys, copy = await asyncio.gather(
        _keys(box), asyncio.to_thread(publish.copy_status, settings.git_dir)
    )
    for r in rows:
        r["editor"] = editing.holder(r["id"])
    return render(
        request,
        "agreements.html",
        rows=rows,
        archived=pv.list_archived(settings),
        box=box,
        keys=keys,
        copy=copy,
        **extra,
    )


# A key submission older than this is no longer waited for; a box release takes minutes.
KEY_WAIT_MAX = 30 * 60


def _key_marker() -> Path:
    return settings.agreements_dir / ".key-submission.json"


def _remember_key(party: str, public_key: str, user: dict[str, str], pr_url: str | None) -> None:
    """A public key submission in flight: every page blocks until production trusts it."""
    try:
        settings.agreements_dir.mkdir(parents=True, exist_ok=True)
        _key_marker().write_text(
            json.dumps(
                {
                    "party": party,
                    "key": public_key,
                    "user": user.get("username"),
                    "pr_url": pr_url,
                    "at": time.time(),
                }
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def _key_wait() -> dict | None:
    """The key submission pages wait for, or None: none, or older than KEY_WAIT_MAX."""
    try:
        marker = json.loads(_key_marker().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if time.time() - float(marker.get("at", 0)) > KEY_WAIT_MAX:
        _forget_key()
        return None
    return marker


def _forget_key() -> None:
    try:
        _key_marker().unlink()
    except OSError:
        pass


def _ui_keys() -> dict[str, str | None]:
    """The party keys in this server's copy of the repository. Signatures are checked
    against production's trust file as GitHub serves it (plan_view.load_trust); the copy
    stands in when GitHub has not answered."""
    try:
        trust = json.loads(settings.trust_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        trust = {}
    return {party: trust.get(f"{party}_key") for party in PARTIES}


async def _keys(box: dict) -> dict:
    """Each party's key on production, in this server's copy and in the box, with a verdict."""
    manifest = box.get("manifest") or {}
    return await deploystatus.keys(settings, _ui_keys(), manifest.get("parties"))


@app.get("/health")
async def health() -> JSONResponse:
    """Where this server stands, without a login and without secrets: its copy of the
    repository against GitHub, whether GitHub answers, and the keys each side trusts."""
    copy = await asyncio.to_thread(publish.copy_status, settings.git_dir)
    production = (
        deploystatus.production_trust_cached(settings)
        if settings.trust_follows_production
        else None
    )
    copy_keys = _ui_keys()
    return JSONResponse(
        {
            "copy": copy,
            "github": deploystatus.github_state(),
            "trust_source": "production on GitHub" if production else "this server's copy",
            "keys": {
                party: {
                    "production": (production or {}).get(f"{party}_key"),
                    "copy": copy_keys[party],
                }
                for party in PARTIES
            },
        }
    )


@app.post("/copy/reset")
async def copy_reset(request: Request) -> Response:
    """Set this server's copy's local changes aside (into a git stash, nothing deleted) and
    catch up with GitHub."""
    if user_of(request) is None:
        return to_login(request)
    path = urlsplit(request.headers.get("referer", "")).path or "/agreements"
    try:
        note = await asyncio.to_thread(publish.set_aside, settings.git_dir)
    except RuntimeError as exc:
        return back(path, err=f"Could not set the local changes aside: {str(exc)[-200:]}")
    moved = await asyncio.to_thread(publish.pull_now, settings.git_dir)
    status = await asyncio.to_thread(publish.copy_status, settings.git_dir)
    if status["last_pull"]["error"]:
        return back(path, err=f"{note}; catch-up still failed: {status['last_pull']['error']}")
    return back(
        path,
        msg=f"{note}. This server's copy is at {status['head']}, "
        + ("moved." if moved else "already up to date."),
    )


@app.post("/copy/pull")
async def copy_pull(request: Request) -> Response:
    """Catch this server's copy up with its branch on GitHub now, and say what happened."""
    if user_of(request) is None:
        return to_login(request)
    path = urlsplit(request.headers.get("referer", "")).path or "/agreements"
    moved = await asyncio.to_thread(publish.pull_now, settings.git_dir)
    status = await asyncio.to_thread(publish.copy_status, settings.git_dir)
    if status["last_pull"]["error"]:
        return back(path, err=f"Catch-up failed: {status['last_pull']['error']}")
    return back(
        path,
        msg=f"This server's copy is at {status['head']}, "
        + ("moved." if moved else "already up to date."),
    )


@app.get("/agreements/new")
async def new_agreement_form(request: Request) -> Response:
    if user_of(request) is None:
        return to_login(request)
    return render(
        request,
        "new_agreement.html",
        form={},
        taken=pv.taken_names(settings),
        agreements=[r["id"] for r in pv.list_agreements(settings)],
    )


@app.post("/agreements/new")
async def new_agreement(request: Request) -> Response:
    if user_of(request) is None:
        return to_login(request)
    form = {k: str(v) for k, v in (await request.form()).items()}
    if form.get("chat") == "1" and not settings.anthropic_api_key:
        return render(
            request,
            "new_agreement.html",
            form=form,
            taken=pv.taken_names(settings),
            agreements=[r["id"] for r in pv.list_agreements(settings)],
            err="AI creation is unavailable on this server. Create a draft manually instead.",
        )
    errors, created = pv.new_agreement(settings, form, user_of(request) or {})
    if errors or created is None:
        return render(
            request,
            "new_agreement.html",
            form=form,
            taken=pv.taken_names(settings),
            agreements=[r["id"] for r in pv.list_agreements(settings)],
            err="; ".join(errors),
        )
    saved(user_of(request))
    chat = "&chat=1" if form.get("chat") == "1" else ""
    return back(f"/a/{created}/plan?edit=1{chat}", msg=f"New agreement {created}.")


def _pins_differ(pre: dict, box: dict) -> bool:
    pins, live = (pre.get("release") or {}).get("image_digest"), box.get("image_digest")
    return bool(pins and live and pins != live)


async def _run_preflight(folder: Path | None) -> dict:
    """The run's preconditions, plus one the files alone cannot tell: the release pins
    must be the release the box runs, or the attestation step fails with the SDK's
    catch-all "model_unavailable". The checkout is pulled once here when the pins and the
    box disagree."""
    pre = runs.preflight(settings, folder)
    box = await box_status(settings.box_url)
    if _pins_differ(pre, box):
        if await asyncio.to_thread(publish.pull_now, settings.git_dir):
            pre = runs.preflight(settings, folder)
    if _pins_differ(pre, box):
        tag = (pre.get("release") or {}).get("tag", "?")
        pre["missing"].append(
            f"the release pins here ({tag}) are not the release the box runs. A release may"
            " still be finishing, or this server has not caught up with it yet;"
            " try again in a minute. Until they agree the box cannot be attested."
        )
        pre["ok"] = False
    return pre


def _runnable(rows: list[dict]) -> list[dict]:
    """The agreements a run can be started on: loaded, no issue, signed by both."""
    return [r for r in rows if r["digest"] and not r["error"] and not r["issue"] and r["signed"]]


RUNS_PAGE_SIZE = 20  # past runs per page on the Runs screen


def _past_runs(qp: Any, chosen: str, logs: str) -> dict:
    """The table of past runs on the Runs screen: filtered by ``rq`` (agreement and input,
    run id, plan digest), ``rby`` (who started it) and ``rstate`` (outcome title), newest
    first, one page of them (``rpage``)."""
    every = runs.list_runs(settings)
    q = qp.get("rq", "").strip()
    by = qp.get("rby", "").strip()
    state = qp.get("rstate", "").strip()
    needle = q.lower()

    def text(r: dict) -> str:
        digest = (r.get("plan") or {}).get("digest") or ""
        return f"{r.get('transcript', '')} {r.get('id', '')} {digest}".lower()

    matched = [
        r
        for r in every
        if (not needle or needle in text(r))
        and (not by or r["by"]["user"] == by)
        and (not state or r["outcome"]["title"] == state)
    ]
    pages = max(1, -(-len(matched) // RUNS_PAGE_SIZE))
    try:
        page = min(max(1, int(qp.get("rpage", "1"))), pages)
    except ValueError:
        page = 1
    keep = {"agreement": chosen, "logs": logs, "rq": q, "rby": by, "rstate": state}
    return {
        "any": bool(every),
        "shown": matched[(page - 1) * RUNS_PAGE_SIZE : page * RUNS_PAGE_SIZE],
        "matched": len(matched),
        "page": page,
        "pages": pages,
        "q": q,
        "by": by,
        "state": state,
        "bys": sorted({r["by"]["user"] for r in every}),
        "states": sorted({r["outcome"]["title"] for r in every}),
        "filters": "".join(f"&{k}={quote(v)}" for k, v in keep.items() if v),
    }


@app.get("/runs")
async def runs_index(request: Request) -> Response:
    if user_of(request) is None:
        return to_login(request)
    qp = request.query_params
    choices = _runnable(pv.list_agreements(settings))
    chosen = qp.get("agreement") or (choices[0]["id"] if choices else "")
    target = pv.for_agreement(settings, chosen) if chosen else None
    folder = target.plan_dir if target else None
    pre = await _run_preflight(folder)
    if folder is None and chosen:
        pre["missing"].insert(0, f"no agreement called {chosen}")
        pre["ok"] = False
    source, source_fixed = _run_source(request, folder)
    all_sessions = proxylogs.sessions(proxylogs.load_index(settings, source))
    # The agreement's input filter decides what is sent: sessions it takes, sessions outside
    # its scope (the box would refuse them), and sessions with nothing to send.
    fits, outside, empty = [], [], []
    named = runs.plan_sessions(folder) if folder else set()
    for s in all_sessions:
        times = [r.time for r in s.records]
        recs = proxylogs.box_records(s.records)
        notes = runs.scope_notes(folder, s.who, min(times), max(times)) if folder else []
        if named and s.id not in named:
            notes = [*notes, "not one of the sessions the agreement names"]
        item = {
            "session": s,
            "notes": notes,
            "records": len(recs),
            "kb": round(len(json.dumps(recs).encode("utf-8")) / 1024, 1),
        }
        (outside if item["notes"] else empty if not recs else fits).append(item)
    totals = {
        "records": sum(i["records"] for i in fits),
        "kb": round(sum(i["kb"] for i in fits), 1),
    }
    return render(
        request,
        "runs.html",
        pre=pre,
        choices=choices,
        chosen=chosen,
        log_source=source,
        log_sources=settings.log_sources,
        source_fixed=source_fixed,
        fits=fits,
        outside=outside,
        empty=empty,
        all_count=len(all_sessions),
        totals=totals,
        scope=runs.plan_scope(folder) if folder else None,
        past=_past_runs(qp, chosen, source.id),
        show_bar=any(c["id"] == chosen for c in choices),
        plan_digest=pre["plan_digest"],
    )


@app.post("/runs/start")
async def runs_start(request: Request) -> Response:
    user = user_of(request)
    if user is None:
        return to_login(request)
    form = await request.form()
    chosen = str(form.get("agreement") or "")
    target = pv.for_agreement(settings, chosen) if chosen else None
    if target is None:
        return back("/runs", err="Pick an agreement signed by both parties.")
    here = f"/runs?agreement={quote(chosen)}"
    pre = await _run_preflight(target.plan_dir)
    if not pre["ok"]:
        return back(here, err="Cannot start: " + "; ".join(pre["missing"]))
    source, _ = _run_source(
        request, target.plan_dir, form
    )  # session ids are unique within one source
    here += f"&logs={source.id}"
    try:
        chosen_sessions = set(form.getlist("session"))
        picked = [
            s
            for s in proxylogs.sessions(proxylogs.load_index(settings, source))
            if s.id in chosen_sessions
        ]
        if not picked:
            return back(here, err="Pick at least one recorded session.")
        records = proxylogs.box_records([r for s in picked for r in s.records])
        if not records:
            return back(here, err="The chosen sessions hold no text to audit.")
        label = (
            f"{chosen} · {len(picked)} recorded session(s) from {source.label}, "
            f"{len(records)} records"
        )
        run_id = runs.start_run(settings, user, chosen, target.plan_dir, records, label=label)
    except (ValueError, OSError) as exc:
        return back(here, err=str(exc))
    sent = (runs.load_run(settings, run_id) or {}).get("plan") or {}
    version = f", version {sent['digest'][7:19]}" if sent.get("digest") else ""
    pv.record_change(
        target, user, "run", "run", f"run {run_id} started ({len(records)} records{version})"
    )
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.get("/runs/{run_id}")
async def run_page(request: Request, run_id: str) -> Response:
    if user_of(request) is None:
        return to_login(request)
    state = runs.load_run(settings, run_id)
    if state is None:
        return back("/runs", err="No such run.")
    return render(request, "run.html", run=state)


@app.get("/runs/{run_id}/plan")
async def run_plan(request: Request, run_id: str) -> Response:
    """The plan version a run sent, read only: the frozen copy in the run folder, not the
    agreement as it is now."""
    if user_of(request) is None:
        return to_login(request)
    state = runs.load_run(settings, run_id)
    if state is None:
        return back("/runs", err="No such run.")
    view, error = load_view(replace(settings, plan_dir=settings.runs_dir / run_id / "plan"))
    return render(
        request,
        "plan.html",
        editor=None,
        view=view,
        error=error,
        edit=False,
        base=f"/a/{state['agreement']}",
        aid=state["agreement"],
        snapshot=state,
    )


@app.get("/attest")
async def attest(request: Request) -> Response:
    """The box's details now live on the Agreements page."""
    return RedirectResponse("/agreements", status_code=303)


@app.get("/plan")
async def legacy_plan() -> Response:
    return RedirectResponse("/agreements", status_code=303)


# --------------------------------------------------------------------------- one agreement


@app.get("/a/{aid}/history")
async def history_page(request: Request, aid: str) -> Response:
    """Who changed what in one agreement: filter by party or kind, twenty at a time."""
    target, redirect = agreement(request, aid)
    if redirect:
        return redirect
    view, error = load_view(target)
    if view is None:
        return back(f"/a/{aid}/plan", err=error or "The plan does not load.")
    q = request.query_params
    who, kind, show_all = q.get("who", ""), q.get("kind", ""), q.get("changes") == "all"
    history = list(view.history)
    if who in PARTIES:
        history = [e for e in history if e.get("party") == who]
    if kind in CHANGE_KINDS:
        history = [e for e in history if e.get("kind") in CHANGE_KINDS[kind]]
    changes = {
        "entries": history if show_all else history[:20],
        "total": len(history),
        "who": who,
        "kind": kind,
        "all": show_all,
        "kinds": list(CHANGE_KINDS),
    }
    return render(request, "history.html", view=view, base=f"/a/{aid}", aid=aid, changes=changes)


@app.get("/a/{aid}/plan")
async def plan(request: Request, aid: str) -> Response:
    target, redirect = agreement(request, aid)
    if redirect:
        return redirect
    view, error = load_view(target)
    q = request.query_params
    edit, editor = _edit_mode(request, aid, q.get("edit") == "1")
    return render(
        request,
        "plan.html",
        editor=editor,
        view=view,
        error=error,
        edit=edit,
        base=f"/a/{aid}",
        aid=aid,
        chat_enabled=bool(settings.anthropic_api_key),
        chat_initial=(
            "Create a plan for this question: " + view.description
            if view and q.get("chat") == "1"
            else ""
        ),
    )


@app.get("/a/{aid}/plan.json")
async def plan_document(request: Request, aid: str) -> Response:
    """The plan for the builder: the document, its digest and whether it may be saved."""
    target, redirect = agreement(request, aid)
    if redirect:
        return JSONResponse({"error": "Please log in again."}, status_code=401)
    view, error = load_view(target)
    if view is None:
        return JSONResponse({"error": error or "the plan does not load"}, status_code=422)
    actor = user_of(request) or {}
    holder = editing.holder(aid) if aid != "plan" else None
    return JSONResponse(
        {
            "plan": view.plan,
            "digest": view.digest,
            "plan_id": view.plan_id,
            "frozen": view.frozen,
            "editable": aid != "plan"
            and not view.frozen
            and bool(holder)
            and holder["user"] == actor.get("username"),
            "issues": list(view.issues),
            "issue": view.issue,
        }
    )


@app.put("/a/{aid}/plan.json")
async def plan_save(request: Request, aid: str) -> Response:
    """Save from the builder. The box's loader checks the plan first; the lease, the frozen
    state and the version the page saw are checked like every other save."""
    actor = user_of(request)
    if actor is None:
        return JSONResponse({"error": "Please log in again."}, status_code=401)
    target = pv.for_agreement(settings, aid)
    if target is None:
        return JSONResponse({"error": "No such agreement."}, status_code=404)
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host"):
        return JSONResponse({"error": "Save from this site's plan builder."}, status_code=403)
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
        return JSONResponse({"error": "Expected a JSON request."}, status_code=415)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 1_000_000:
            return JSONResponse({"error": "The plan is too large."}, status_code=413)
    try:
        payload = json.loads(body)
    except ValueError:
        return JSONResponse({"error": "Could not read the request."}, status_code=422)
    if not isinstance(payload, dict) or "plan" not in payload:
        return JSONResponse({"error": "Expected {plan, expect}."}, status_code=422)
    async with _lock(aid):
        view, error = load_view(target)
        if view is None:
            return JSONResponse({"error": error or "plan does not load"}, status_code=422)
        if view.frozen:
            return JSONResponse({"error": FROZEN}, status_code=409)
        if held := _held(aid, actor):
            return JSONResponse({"error": held}, status_code=409)
        if not editing.holder(aid):
            return JSONResponse(
                {"error": "Press Edit this plan first; the agreement is not taken for editing."},
                status_code=409,
            )
        if stale := _stale(view, str(payload.get("expect") or "")):
            return JSONResponse({"error": stale}, status_code=409)
        digest, issues = pv.save_plan(target, payload["plan"], actor)
        if digest is None:
            return JSONResponse(
                {"error": "The box would reject this plan; nothing was saved.", "issues": issues},
                status_code=422,
            )
        saved(actor)
        return JSONResponse({"digest": digest, "message": "Saved. This is a new version."})


@app.post("/archive/{name}/delete")
async def delete_archived(request: Request, name: str) -> Response:
    """Remove an agreement in the previous plan format from agreements/_archive/."""
    if user_of(request) is None:
        return to_login(request)
    problem = pv.delete_archived(settings, name)
    if problem:
        return back("/agreements", err=problem)
    return back("/agreements", msg=f"Archived agreement {name} deleted.")


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
    other = editing.acquire(aid, user_of(request) or {})
    if other:
        return back(f"/a/{aid}/plan", err=editing.describe(other) + ". Try again later.")
    removed = pv.delete_signatures(target, user_of(request))
    # The version is unchanged, so the server's lab signature comes straight back.
    if pv.ensure_lab_signature(target, user_of(request)):
        removed -= 1
    saved(user_of(request))
    note = f" {removed} signature file(s) removed." if removed > 0 else ""
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


@app.post("/a/{aid}/delete")
async def delete_agreement(request: Request, aid: str) -> Response:
    target, redirect = agreement(request, aid, write=True)
    if redirect:
        return redirect
    problem = pv.delete_agreement(settings, aid)
    if problem:
        return back("/agreements", err=problem)
    saved(user_of(request))
    return back("/agreements", msg=f"{aid} deleted.")


def _edit_mode(request: Request, aid: str, wanted: bool) -> tuple[bool, dict | None]:
    """Edit mode belongs to the lease holder. Returns (edit, the other holder or None)."""
    actor = user_of(request) or {}
    if not wanted:
        # Opening the agreement outside edit mode is leaving it: every save is already on
        # disk, so nothing is lost and nobody is kept waiting (and Run is not blocked).
        editing.release(aid, actor)
        return False, editing.holder(aid)
    other = editing.blocked_by(aid, actor)
    if other:
        return False, other
    if editing.acquire(aid, actor):
        return False, editing.holder(aid)
    return True, None


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


def _sign_next(aid: str, next_path: str) -> str:
    """Where a signature action returns to: the page the Sign button was on, if it names
    one on this site, else the Sign screen."""
    if next_path.startswith("/") and not next_path.startswith("//"):
        return next_path
    return f"/a/{aid}/sign"


# What the Sign button found missing in the browser before it sent someone here.
SIGN_WHY = {
    "unsupported": "This browser cannot sign: it cannot make Ed25519 keys (needs Safari 17, "
    "Firefox 130 or Chrome 137, or newer). Use the commands under Other ways to sign and paste "
    "the signature below.",
    "none": "Signing is not set up in this browser yet. Set it up below, once. After that, "
    "Sign on the plan signs straight away.",
    "waiting": "The box does not trust your key yet. Submit it below if you have not; Sign "
    "works once the release is done.",
    "old": "This browser has your key from before root keys. Continue with it below, then "
    "Sign on the plan signs straight away.",
    "stale": "This browser's key is not one the box trusts. Set up signing again below, or "
    "use your key file.",
}


@app.get("/a/{aid}/sign")
async def sign(request: Request, aid: str) -> Response:
    target, redirect = agreement(request, aid, write=True)
    if redirect:
        return redirect
    editing.release(aid, user_of(request) or {})  # going to sign is leaving edit mode
    view, error = load_view(target)
    keys, copy = await asyncio.gather(
        _keys(await box_status(settings.box_url)),
        asyncio.to_thread(publish.copy_status, settings.git_dir),
    )
    return render(
        request,
        "sign.html",
        view=view,
        error=error,
        base=f"/a/{aid}",
        aid=aid,
        keys=keys,
        copy=copy,
        auto_sign=bool(settings.lab_signing_key),
        issuer_parties=[p for p in PARTIES if pv.server_certifies(settings, p)],
        server_problems={
            p: pv.server_problem(settings, p) or pv.issuer_problem(settings, p) for p in PARTIES
        },
        why=SIGN_WHY.get(request.query_params.get("why", "")),
    )


@app.post("/a/{aid}/sign/upload")
async def sign_upload(
    request: Request,
    aid: str,
    party: Annotated[str, Form()],
    signature: Annotated[str, Form()] = "",
    certificate: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",
) -> Response:
    # The defaults keep an empty paste from being a 422; the browser sends "" for an empty box.
    # `next` is the page the Sign button was on; a problem always shows on the Sign screen.
    target, redirect = agreement(request, aid, write=True)
    if redirect:
        return redirect
    user = user_of(request)
    here = f"/a/{aid}/sign"
    if user is None or party != user["party"]:
        return back(here, err="Paste only your own party's signature.")
    if not signature.strip():
        return back(here, err="Paste the line the sign command printed first.")
    status, detail = pv.write_signature(target, party, signature.encode("utf-8"), user, certificate)
    if status != "ok":
        return back(here, err=f"Signature not accepted: {detail}")
    editing.clear(aid)  # a signature locks the plan, so no edit session is open any more
    saved(user)
    return back(_sign_next(aid, next), msg=f"{party} signature verified and stored.")


@app.post("/sign/certificate")
async def sign_certificate(request: Request) -> Response:
    """Certify the public key a logged-in member's browser just made, with the party root
    this server holds (prototype: AIAB_AUDITOR_ROOT_KEY). JSON in and out; a form post from
    another site cannot reach it (SameSite cookies, and a cross-site JSON post needs CORS)."""
    user = user_of(request)
    if user is None:
        return JSONResponse({"error": "Log in first."}, status_code=401)
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        return JSONResponse({"error": "Send JSON."}, status_code=415)
    try:
        body = await request.json()
        party, signing_key = str(body["party"]), str(body["signing_key"])
    except (ValueError, KeyError, TypeError):
        return JSONResponse({"error": "Send party and signing_key."}, status_code=422)
    if party != user["party"]:
        return JSONResponse({"error": "Only your own party's key can be certified."}, 403)
    status, result = pv.issue_certificate(settings, party, signing_key, user)
    if status != "ok":
        return JSONResponse({"error": result}, status_code=409)
    return JSONResponse({"certificate": result})


@app.post("/a/{aid}/sign/remove")
async def sign_remove(
    request: Request, aid: str, party: Annotated[str, Form()], next: Annotated[str, Form()] = ""
) -> Response:
    """Re-sign: remove your own signature for this version; the Sign button comes back."""
    target, redirect = agreement(request, aid, write=True)
    if redirect:
        return redirect
    user = user_of(request)
    here = f"/a/{aid}/sign"
    if user is None or party != user["party"]:
        return back(here, err="You can only remove your own signature.")
    if not pv.remove_signature(target, party, user):
        return back(here, err="There is no signature of yours to remove.")
    saved(user)
    return back(_sign_next(aid, next), msg="Your signature is removed. Sign this version again.")


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
        return back(here, err="You can only replace your own party's root key.")
    if pv.server_signs(settings, party) or pv.server_certifies(settings, party):
        held = pv.SERVER_KEY_ENV.get(party) or (pv.ROOT_KEY_ENV[party],)
        return back(
            here,
            err=f"This server signs for the {party} under the current root; a new root would "
            f"stop that. Unset {' and '.join(held)} first.",
        )
    public_key, problem = pv.normalize_public_key(public_key)
    if problem or public_key is None:
        return back(here, err=problem or "that is not an Ed25519 public key")
    if pv.load_trust(settings).get(f"{party}_key") == public_key:
        return back(here, msg=f"Production already trusts this {party} root. Nothing to submit.")
    pushed = publish.push_key_change(
        settings.git_dir, party, public_key, user, base=settings.deploy_base
    )
    if pushed.error:
        return back(here, err=f"The {party} key was not submitted: {pushed.error}")
    pv.record_change(target, user, "key", f"root key: {party}", f"submitted {public_key}")
    where = pushed.pr_url or pushed.compare_url or f"branch {pushed.branch}"
    _remember_key(party, public_key, user, pushed.pr_url)  # every page blocks until the box has it
    return back(
        here,
        msg=f"The {party} root key is submitted ({where}). The check merges it within a minute"
        " and the box is released with it; every page waits until the box trusts it.",
    )


@app.get("/keys/status.json")
async def keys_status_json(request: Request) -> JSONResponse:
    """Progress of the key submission in flight: the pull request on GitHub, then the box
    release. Clears the marker once the box trusts the key (or, with no box configured,
    once production does), or the check rejected it, so pages stop blocking."""
    if user_of(request) is None:
        return JSONResponse({"error": "login"}, status_code=401)
    wait = _key_wait()
    if wait is None:
        return JSONResponse({"waiting": False})
    deploystatus.forget_keys()
    box = await box_status(settings.box_url)
    manifest = box.get("manifest") or {}
    keys = await deploystatus.keys(settings, _ui_keys(), manifest.get("parties"))
    info = keys["parties"].get(wait["party"]) or {}
    on_production = info.get("production") == wait["key"]
    # A box that is configured but not answering is most likely restarting with the new
    # image: keep waiting (KEY_WAIT_MAX caps it) rather than declare the key trusted.
    in_box = info.get("box") == wait["key"] if box.get("configured") else True
    trusted = on_production and in_box
    settled = trusted or (not info.get("busy") and info.get("tone") == "bad")
    if settled:
        _forget_key()
    label = info.get("label")
    if not settled and not info.get("busy"):
        label = "waiting for GitHub to show the pull request"
    if not settled and on_production and box.get("configured") and not manifest:
        label = "trusted on production · the box is restarting with the new key"
    return JSONResponse(
        {"waiting": not settled, "party": wait["party"], "label": label, "tone": info.get("tone")}
    )
