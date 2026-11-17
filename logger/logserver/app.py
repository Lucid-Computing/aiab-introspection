"""The two things the agreement UI needs from the logger, and nothing else:

    GET  /v1/sessions   the recorded sessions and their calls as metadata: who, when, model,
                        status, sizes, how many records. Never what was said.
    POST /v1/export     {"sessions": [ids]}: the records of those sessions, each content
                        sealed to the box's current key. Only the box can open them.

Both take the caller's key as ``x-log-key``. Each box has its own key, and the key decides
which box an export is sealed to (``logserver.config``). Also ``GET /v1/status`` (the last
read of the proxy's files, and any file that could not be read), ``POST /v1/sync`` (read
them again now) and ``GET /healthz`` (no key). Every route answers from the last read; none
reads a file.
With ``PLANT_HUB_URL`` and ``PLANT_HUB_KEY`` set, the RSI plant's hub is a source too
(``logserver.plant``), next to the proxy's directory or alone; the routes are the same, and a
box can be kept to some of the sources (``<NAME>_SOURCES``).
No text is held in memory: an export reads the chosen calls' text from the disk.
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
from logserver.config import Settings, Target
from logserver.plant import PlantStore
from logserver.sources import Sources
from logserver.store import Store, StoreError


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # no field may name another box or key
    sessions: list[str] = Field(min_length=1, max_length=1000)
    # Where this export's record numbers start. A run that joins exports from several log
    # servers gives each its own range: the box refuses two records at one position.
    offset: int = Field(default=0, ge=0, le=2**52)


def create_app(
    settings: Settings,
    store: Store | PlantStore | Sources | None = None,
    boxes: dict[str, Box] | None = None,
    background: bool = True,
) -> FastAPI:
    store = store or _store(settings)
    boxes = boxes or {t.name: Box(t.box_host, t.box_repo) for t in settings.boxes}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if background:
            store.start()
        yield

    app = FastAPI(title="AIAB logger", lifespan=lifespan, docs_url=None, redoc_url=None)

    def key(x_log_key: str = Header(default="")) -> Target:
        """The box this caller's key belongs to. The key decides it; a request cannot."""
        found = None
        for target in settings.boxes:  # every key is compared, whichever one matches
            if hmac.compare_digest(x_log_key.encode(), target.api_key.encode()):
                found = target
        if found is None:
            raise HTTPException(401, "missing or wrong x-log-key")
        return found

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    def seen(target: Target) -> list:
        """The calls this caller may see: those of the sources its box is given."""
        return [c for c in store.calls() if c.source in target.sources]

    @app.get("/v1/status")
    def status(target: Target = Depends(key)):  # noqa: B008 - FastAPI's way to ask for it
        return {
            **store.status,
            "calls": len(seen(target)),
            "box": target.box_host,
            "sources": [s for s in settings.sources if s in target.sources],
        }

    @app.post("/v1/sync", dependencies=[Depends(key)])
    def sync():
        try:
            return store.refresh()
        except StoreError as error:
            raise HTTPException(503, str(error)) from None

    @app.get("/v1/sessions")
    def sessions(who: str = "", target: Target = Depends(key)):  # noqa: B008
        found = calls.sessions(seen(target))
        whos = sorted({s.who for s in found})
        if who:
            found = [s for s in found if s.who == who]
        return {"whos": whos, "sessions": [s.metadata() for s in found]}

    @app.post("/v1/export")
    async def export(body: ExportRequest, target: Target = Depends(key)):  # noqa: B008
        by_id = {s.id: s for s in calls.sessions(seen(target))}
        unknown = [i for i in body.sessions if i not in by_id]
        if unknown:
            raise HTTPException(404, {"error": "unknown_sessions", "sessions": unknown})
        wanted = list(dict.fromkeys(body.sessions))
        chosen = [c for i in wanted for c in by_id[i].calls]
        try:  # the text is read from the disk only now, for these calls
            chosen = await run_in_threadpool(store.with_text, chosen)
        except StoreError as error:
            raise HTTPException(503, str(error)) from None
        records = calls.box_records(chosen, body.offset)
        if not records:
            raise HTTPException(422, "the chosen sessions hold no text to audit")
        try:
            sealed = await run_in_threadpool(boxes[target.name].seal, records)
        except BoxError as error:
            raise HTTPException(502, str(error)) from None
        return {**sealed, "sessions": wanted}

    return app


def _store(settings: Settings) -> Store | PlantStore | Sources:
    """The store for the sources this server reads: the one, or the two joined."""
    made = {"proxy": Store, "plant": PlantStore}
    stores = {name: made[name](settings) for name in settings.sources}
    return next(iter(stores.values())) if len(stores) == 1 else Sources(stores)


def main() -> FastAPI:
    """For ``uvicorn --factory logserver.app:main``: settings from the environment."""
    return create_app(config.load())
