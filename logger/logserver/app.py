"""The two things the agreement UI needs from the logger, and nothing else:

    GET  /v1/sessions   the recorded sessions and their calls as metadata: who, when, model,
                        status, sizes, how many records. Never what was said.
    POST /v1/export     {"sessions": [ids]}: the records of those sessions, each content
                        sealed to the box's current key. Only the box can open them.

Both take this server's key as ``x-log-key``. Also ``GET /v1/status`` (the last sync),
``POST /v1/sync`` (ask the proxy for new segments now) and ``GET /healthz`` (no key).
The export is not signed here: the agreement UI signs the sealed records as it does today.
"""

from __future__ import annotations

import hmac
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from logserver import calls, config
from logserver.box import Box, BoxError
from logserver.config import Settings
from logserver.store import ProxyError, Store


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # no field may name another box or key
    sessions: list[str] = Field(min_length=1, max_length=1000)


def create_app(
    settings: Settings, store: Store | None = None, box: Box | None = None, background: bool = True
) -> FastAPI:
    store = store or Store(settings)
    box = box or Box(settings.box_host, settings.box_repo)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if background:
            store.start()
        yield

    app = FastAPI(title="AIAB logger", lifespan=lifespan, docs_url=None, redoc_url=None)

    def key(x_log_key: str = Header(default="")) -> None:
        if not hmac.compare_digest(x_log_key.encode(), settings.api_key.encode()):
            raise HTTPException(401, "missing or wrong x-log-key")

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/v1/status", dependencies=[Depends(key)])
    def status():
        return {**store.status, "calls": len(store.load()), "box": settings.box_host}

    @app.post("/v1/sync", dependencies=[Depends(key)])
    def sync():
        try:
            return store.sync()
        except ProxyError as error:
            raise HTTPException(502, str(error)) from None

    @app.get("/v1/sessions", dependencies=[Depends(key)])
    def sessions(who: str = ""):
        found = calls.sessions(store.load())
        whos = sorted({s.who for s in found})
        if who:
            found = [s for s in found if s.who == who]
        return {"whos": whos, "sessions": [s.metadata() for s in found]}

    @app.post("/v1/export", dependencies=[Depends(key)])
    async def export(body: ExportRequest):
        by_id = {s.id: s for s in calls.sessions(store.load())}
        unknown = [i for i in body.sessions if i not in by_id]
        if unknown:
            raise HTTPException(404, {"error": "unknown_sessions", "sessions": unknown})
        wanted = list(dict.fromkeys(body.sessions))
        records = calls.box_records([c for i in wanted for c in by_id[i].calls])
        if not records:
            raise HTTPException(422, "the chosen sessions hold no text to audit")
        try:
            sealed = await run_in_threadpool(box.seal, records)
        except BoxError as error:
            raise HTTPException(502, str(error)) from None
        return {**sealed, "sessions": wanted}

    return app


def main() -> FastAPI:
    """For ``uvicorn --factory logserver.app:main``: settings from the environment."""
    return create_app(config.load())
