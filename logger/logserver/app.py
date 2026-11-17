"""The two things the agreement UI needs from the logger, and nothing else:

    GET  /v1/sessions   the recorded sessions and their calls as metadata: who, when, model,
                        status, sizes, how many records. Never what was said.
    POST /v1/export     {"sessions": [ids]}: the records of those sessions, each content
                        sealed to the box's current key. Only the box can open them.

Both take the caller's key as ``x-log-key``. Each box has its own key, and the key decides
which box an export is sealed to (``logserver.config``). Also ``GET /v1/status`` (the last
read of the proxy's files, and any file that could not be read), ``POST /v1/sync`` (read
them again now) and ``GET /healthz`` (no key). Every route answers from the last read, except
an export: the index keeps no text, so it reads the chosen calls back from the proxy's files,
checked against what was listed (409 if one changed, 410 if its file is gone).
The export is not signed here: the agreement UI signs the sealed records as it does today.
"""

from __future__ import annotations

import hmac
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from logserver import calls, config
from logserver.box import Box, BoxError
from logserver.config import Settings, Target
from logserver.store import ChangedError, GoneError, Store, StoreError


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # no field may name another box or key
    sessions: list[str] = Field(min_length=1, max_length=1000)
    # Where this export's record numbers start. A run that joins exports from several log
    # servers gives each its own range: the box refuses two records at one position.
    offset: int = Field(default=0, ge=0, le=2**52)


def create_app(
    settings: Settings,
    store: Store | None = None,
    boxes: dict[str, Box] | None = None,
    background: bool = True,
) -> FastAPI:
    store = store or Store(settings)
    boxes = boxes or {t.name: Box(t.box_host, t.box_repo) for t in settings.boxes}
    # One export reads and holds its plaintext at a time; a box's own attestation is not in it.
    export_lock = threading.Lock()

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

    @app.get("/v1/status")
    def status(target: Target = Depends(key)):  # noqa: B008 - FastAPI's way to ask for it
        return {**store.status, "calls": len(store.calls()), "box": target.box_host}

    @app.post("/v1/sync", dependencies=[Depends(key)])
    def sync():
        try:
            return store.refresh()
        except StoreError as error:
            raise HTTPException(503, str(error)) from None

    @app.get("/v1/sessions", dependencies=[Depends(key)])
    def sessions(who: str = ""):
        found = calls.sessions(store.calls())
        whos = sorted({s.who for s in found})
        if who:
            found = [s for s in found if s.who == who]
        return {"whos": whos, "sessions": [s.metadata() for s in found]}

    # A plain def: FastAPI runs it in its threadpool, so neither the selection (every call is
    # grouped) nor the reading and sealing ever runs on the event loop.
    @app.post("/v1/export")
    def export(body: ExportRequest, target: Target = Depends(key)):  # noqa: B008
        by_id = {s.id: s for s in calls.sessions(store.calls())}
        unknown = [i for i in body.sessions if i not in by_id]
        if unknown:
            raise HTTPException(404, {"error": "unknown_sessions", "sessions": unknown})
        wanted = list(dict.fromkeys(body.sessions))
        chosen = [c for i in wanted for c in by_id[i].calls]
        if not any(c.kinds for c in chosen):
            raise HTTPException(422, "the chosen sessions hold no text to audit")
        box = boxes[target.name]
        try:
            identity = box.identity()  # a box that is down fails here, before any read
            with export_lock:
                texts = store.texts(chosen)
                records = calls.box_records(list(zip(chosen, texts, strict=True)), body.offset)
                sealed = box.seal(records, identity)
        except BoxError as error:
            raise HTTPException(502, str(error)) from None
        except GoneError as error:
            raise HTTPException(
                410,
                f"{', '.join(_sessions_of(error.calls, by_id))}: {error}; the proxy no longer "
                "has the file, so these calls can be listed but not exported",
            ) from None
        except ChangedError as error:
            raise HTTPException(
                409, f"the proxy's log changed since it was listed ({error}); list again"
            ) from None
        except StoreError as error:
            raise HTTPException(503, str(error)) from None
        return {**sealed, "sessions": wanted}

    return app


def _sessions_of(gone: list, by_id: dict) -> list[str]:
    """The sessions holding these calls, as listed."""
    where = {c.where for c in gone}
    return [i for i, s in by_id.items() if any(c.where in where for c in s.calls)]


def main() -> FastAPI:
    """For ``uvicorn --factory logserver.app:main``: settings from the environment."""
    return create_app(config.load())
