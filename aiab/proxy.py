"""Admit supported text requests, enforce policy, and forward to a fixed origin."""

from __future__ import annotations

import asyncio
import inspect
import math
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from aiab import anthropic
from aiab.classifier import (
    AttestationError,
    ClassificationError,
    Classifier,
    ClassifyResult,
    TinfoilLLMClassifier,
    make_classifier,
    serialize_messages,
    strict_json_loads,
)
from aiab.config import Config, ConfigurationError
from aiab.logging_guard import configure_logging, event
from aiab.reporter import Reporter, Ticket
from aiab.sampler import Sampler

_CHAT_FIELDS = {
    "model",
    "messages",
    "stream",
    "temperature",
    "top_p",
    "n",
    "max_tokens",
    "max_completion_tokens",
    "presence_penalty",
    "frequency_penalty",
    "stop",
    "seed",
}
_CORS_METHODS = {
    "/v1/chat/completions": "POST",
    "/v1/models": "GET",
    "/aiab/health": "GET",
}
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def upstream_response(upstream: httpx.Response) -> Response:
    # httpx has decoded content/transfer encodings, so recalculate the length.
    excluded = _HOP_BY_HOP_HEADERS | {"content-encoding", "content-length"}
    excluded.update(
        name.strip().lower() for name in upstream.headers.get("connection", "").split(",")
    )
    response = Response(upstream.content, status_code=upstream.status_code)
    response.raw_headers.extend(
        (name, value)
        for name, value in upstream.headers.raw
        if name.decode("ascii").lower() not in excluded
    )
    return response


def validate_generation_options(body: dict[str, Any]) -> None:
    """Only scalar generation options may accompany the fully inspected messages."""
    integers = {"n", "max_tokens", "max_completion_tokens", "seed"}
    numbers = {"temperature", "top_p", "presence_penalty", "frequency_penalty"}
    for name in integers & body.keys():
        if type(body[name]) is not int:
            raise ValueError("Generation counts must be integers")
        if name != "seed" and body[name] < 1:
            raise ValueError("Generation counts must be positive")
    for name in numbers & body.keys():
        value = body[name]
        if type(value) not in {int, float}:
            raise ValueError("Generation parameters must be numeric")
        try:
            if not math.isfinite(value):
                raise ValueError("Generation parameters must be finite")
        except OverflowError:
            raise ValueError("Generation parameter outside supported range") from None
    if "stop" in body:
        stop = body["stop"]
        if not isinstance(stop, str) and not (
            isinstance(stop, list) and all(isinstance(item, str) for item in stop)
        ):
            raise ValueError("Stop sequences must be text")


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"type": "invalid_request_error", "code": code, "message": message}},
        status_code=status,
    )


class Runtime:
    def __init__(self, config: Config, classifier: Classifier, client: httpx.AsyncClient) -> None:
        self.config = config
        self.classifier = classifier
        self.client = client
        self.reporter = Reporter(config, classifier)
        self.sampler = Sampler(config.sample_rate)
        self.healthy = False
        self._queue: asyncio.Queue[tuple[Ticket, str, str]] = asyncio.Queue(
            config.monitor_queue_limit
        )
        self._workers: list[asyncio.Task[None]] = []
        self._slots = asyncio.Semaphore(config.classifier_concurrency)
        self._executor = ThreadPoolExecutor(max_workers=config.classifier_concurrency)
        self._calls: set[asyncio.Future[Any]] = set()

    async def start(self) -> None:
        verify = getattr(self.classifier, "verify", None)
        if verify is not None:
            try:
                async with asyncio.timeout(self.config.classifier_attempt_timeout_seconds):
                    result = verify()
                    if inspect.isawaitable(result):
                        await result
            except Exception:
                event("startup_failed")
                if self.config.aiab_mode == "tinfoil":
                    raise RuntimeError("Classifier startup verification failed") from None
                return
        self.healthy = True
        self._workers = [
            asyncio.create_task(self._monitor_worker()) for _ in range(self.config.monitor_workers)
        ]
        self._workers.append(asyncio.create_task(self._period_worker()))
        event("startup_ready")

    async def close(self) -> None:
        self.healthy = False
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        while not self._queue.empty():
            ticket, _, _ = self._queue.get_nowait()
            self.reporter.error(ticket)
            self._queue.task_done()
        for call in tuple(self._calls):
            if isinstance(call, asyncio.Task):
                call.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)
        close = getattr(self.classifier, "close", None)
        if close is not None:
            await asyncio.to_thread(close)

    async def _period_worker(self) -> None:
        while True:
            await asyncio.sleep(0.1)
            self.reporter.finalize_due()

    def _call_done(self, call: asyncio.Future[Any]) -> None:
        self._calls.discard(call)
        self._slots.release()
        if not call.cancelled():
            # Consume even abandoned exceptions, without leaking their text to asyncio logs.
            failure = call.exception()
            if isinstance(failure, AttestationError):
                self.healthy = False
                event("attestation_failed")

    async def _invoke(self, prompt: str, response: str | None) -> ClassifyResult:
        if inspect.iscoroutinefunction(self.classifier.classify):
            result = self.classifier.classify(prompt, response)
        else:
            thread = asyncio.get_running_loop().run_in_executor(
                self._executor, self.classifier.classify, prompt, response
            )
            try:
                result = await asyncio.shield(thread)
            except asyncio.CancelledError:
                # A cancelled request must retain capacity until its thread exits,
                # including when shutdown cancels this task again in the meantime.
                while not thread.done():
                    try:
                        await asyncio.shield(thread)
                    except asyncio.CancelledError:
                        continue
                result = thread.result()
                if inspect.isawaitable(result):
                    abandoned = asyncio.ensure_future(result)
                    abandoned.cancel()
                    await asyncio.gather(abandoned, return_exceptions=True)
                raise
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, ClassifyResult):
            raise ClassificationError("Invalid classification result")
        return result

    async def _attempt(self, prompt: str, response: str | None) -> ClassifyResult:
        await self._slots.acquire()
        call = asyncio.create_task(self._invoke(prompt, response))
        self._calls.add(call)
        call.add_done_callback(self._call_done)
        try:
            return await asyncio.shield(call)
        except asyncio.CancelledError:
            call.cancel()
            raise

    async def classify(self, ticket: Ticket, prompt: str, response: str | None) -> ClassifyResult:
        for _ in range(self.config.classifier_attempts):
            if not self.healthy:
                raise AttestationError("Classifier is unavailable")
            remaining = ticket.deadline - self.reporter.clock()
            if remaining <= 0:
                break
            try:
                async with asyncio.timeout(
                    min(remaining, self.config.classifier_attempt_timeout_seconds)
                ):
                    return await self._attempt(prompt, response)
            except AttestationError:
                self.healthy = False
                event("attestation_failed")
                raise
            except Exception:
                # Never log provider error strings: they may echo the input or credentials.
                continue
        event("classifier_error")
        raise ClassificationError("Classification failed")

    def enqueue(self, ticket: Ticket, prompt: str, response: str) -> None:
        if not self.reporter.live(ticket):
            self.reporter.error(ticket)
            return
        try:
            self._queue.put_nowait((ticket, prompt, response))
        except asyncio.QueueFull:
            self.reporter.error(ticket)
            event("queue_exhausted")

    async def _monitor_worker(self) -> None:
        while True:
            ticket, prompt, response = await self._queue.get()
            try:
                result = await self.classify(ticket, prompt, response)
                self.reporter.classified(ticket, result)
            except (Exception, asyncio.CancelledError):
                self.reporter.error(ticket)
                if asyncio.current_task().cancelling():
                    raise
            finally:
                self._queue.task_done()
                # Release body references before idling on the next queue read.
                del prompt, response

    async def forward(
        self, ticket: Ticket, body: bytes, anthropic_body: dict[str, Any] | None = None
    ) -> httpx.Response:
        if not self.healthy or not self.reporter.forwarded(ticket):
            raise ClassificationError("Request deadline or verification gate closed")
        async with asyncio.timeout(max(0, ticket.deadline - self.reporter.clock())):
            if self.config.inference_provider == "anthropic":
                if anthropic_body is None:
                    raise ValueError("Missing validated Anthropic request")
                return await anthropic.complete(self.client, self.config, anthropic_body)
            return await self.client.post(
                self.config.inference_url + "/v1/chat/completions",
                content=body,
                headers={"content-type": "application/json"},
                timeout=self.config.upstream_timeout_seconds,
            )


def create_app(
    config: Config | None = None,
    *,
    classifier: Classifier | None = None,
    upstream_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    settings = config or Config.from_env()
    settings.validate()
    configure_logging(settings.log_level)
    implementation = classifier or make_classifier(settings)
    if (settings.aiab_mode == "tinfoil" or settings.tinfoil_api_key) and not isinstance(
        implementation, TinfoilLLMClassifier
    ):
        raise ConfigurationError("SDK classifier required when a Tinfoil credential is configured")
    client = upstream_client or httpx.AsyncClient(follow_redirects=False, trust_env=False)
    runtime = Runtime(settings, implementation, client)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            await runtime.start()
            yield
        finally:
            await runtime.close()
            if upstream_client is None:
                await client.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.router.redirect_slashes = False
    app.state.runtime = runtime

    @app.middleware("http")
    async def privacy_and_cors(request: Request, call_next: Any) -> Response:
        allowed_method = _CORS_METHODS.get(request.url.path)
        requested_method = (
            request.headers.get("access-control-request-method", allowed_method)
            if request.method == "OPTIONS"
            else request.method
        )
        cors = bool(
            allowed_method
            and requested_method == allowed_method
            and settings.cors_allow_origin
            and request.headers.get("origin") == settings.cors_allow_origin
        )
        if request.method == "OPTIONS" and cors:
            response = Response(status_code=204)
        else:
            try:
                response = await call_next(request)
            except Exception:
                event("internal_error")
                response = error_response(500, "internal_error", "Internal AIAB error")
        if cors:
            response.headers["access-control-allow-origin"] = settings.cors_allow_origin
            vary = response.headers.get("vary")
            response.headers["vary"] = f"{vary}, Origin" if vary else "Origin"
            response.headers["access-control-allow-methods"] = f"{allowed_method}, OPTIONS"
            response.headers["access-control-allow-headers"] = "Content-Type, Authorization"
        return response

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> Response:
        if not runtime.healthy:
            return error_response(
                503, "classifier_unavailable", "Classifier verification unavailable"
            )
        ticket: Ticket | None = None
        try:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > settings.max_request_bytes:
                    return error_response(413, "request_too_large", "Request exceeds size limit")
            decoded = strict_json_loads(bytes(body))
            if not isinstance(decoded, dict) or set(decoded) - _CHAT_FIELDS:
                raise ValueError("Unsupported request fields")
            if type(decoded.get("stream", False)) is not bool or decoded.get("stream", False):
                event("stream_rejected")
                raise ValueError("Streaming is not supported; set stream to false")
            if not isinstance(decoded.get("model"), str) or not decoded["model"]:
                raise ValueError("A model identifier is required")
            prompt = serialize_messages(decoded.get("messages"))
            validate_generation_options(decoded)
            anthropic_body = (
                anthropic.prepare_request(decoded, settings)
                if settings.inference_provider == "anthropic"
                else None
            )
        except (ValueError, TypeError, UnicodeError):
            # Static error text prevents reflecting rejected content or validation internals.
            return error_response(
                400,
                "unsupported_request",
                (
                    "Use the configured model, non-streaming text, leading system messages and "
                    "user/assistant history starting and ending with user; tools and developer "
                    "roles are unsupported. Use n=1, max_tokens or max_completion_tokens=1..2048, "
                    "temperature or top_p=0..1, and at most four text stop sequences."
                    if settings.inference_provider == "anthropic"
                    else "Use non-streaming chat with a model and text-only messages; "
                    "tools are unsupported"
                ),
            )
        selected = settings.enforcement_mode == "block" or runtime.sampler.should_sample("")
        ticket = runtime.reporter.admit(selected=selected)
        try:
            if settings.enforcement_mode == "block":
                result = await runtime.classify(ticket, prompt, None)
                block = result.flagged and result.confidence >= settings.block_threshold
                if not runtime.reporter.classified(ticket, result, block=block):
                    raise ClassificationError("Request processing deadline exceeded")
                if block:
                    return JSONResponse(
                        {
                            "error": {
                                "type": "policy_violation",
                                "code": "aiab_blocked",
                                "message": "Request blocked by AIAB policy: classified as AI "
                                f"capabilities research (confidence {result.confidence:.2f}).",
                                "classifier_id": implementation.id,
                                "confidence": result.confidence,
                            }
                        },
                        status_code=403,
                    )
            upstream = await runtime.forward(ticket, bytes(body), anthropic_body)
            if selected and settings.enforcement_mode == "monitor":
                if upstream.is_success:
                    runtime.enqueue(ticket, prompt, upstream.text)
                else:
                    runtime.reporter.error(ticket)
            return upstream_response(upstream)
        except (ClassificationError, asyncio.CancelledError):
            runtime.reporter.error(ticket)
            if asyncio.current_task().cancelling():
                raise
            return error_response(503, "classifier_unavailable", "Classification unavailable")
        except Exception:
            runtime.reporter.error(ticket)
            event("upstream_error")
            return error_response(502, "upstream_unavailable", "Inference server unavailable")

    @app.get("/v1/models")
    async def models() -> Response:
        if settings.inference_provider == "anthropic":
            return JSONResponse(
                {
                    "object": "list",
                    "data": [
                        {
                            "id": settings.anthropic_model,
                            "object": "model",
                            "created": 0,
                            "owned_by": "anthropic",
                        }
                    ],
                }
            )
        try:
            upstream = await client.get(
                settings.inference_url + "/v1/models", timeout=settings.upstream_timeout_seconds
            )
            return upstream_response(upstream)
        except Exception:
            event("upstream_error")
            return error_response(502, "upstream_unavailable", "Inference server unavailable")

    @app.get("/aiab/manifest")
    async def manifest() -> dict[str, Any]:
        return runtime.reporter.manifest

    @app.get("/aiab/pubkey")
    async def pubkey() -> dict[str, str]:
        return runtime.reporter.public_identity

    @app.get("/aiab/reports")
    async def reports() -> list[dict[str, str]]:
        return runtime.reporter.reports

    @app.get("/aiab/reports/latest")
    async def latest() -> Any:
        available = runtime.reporter.reports
        if not available:
            return error_response(404, "no_finalized_reports", "No finalized reporting period yet")
        return available[-1]

    @app.get("/aiab/health")
    async def health() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ready" if runtime.healthy else "unavailable",
                "enforcement_mode": settings.enforcement_mode,
                "aiab_mode": settings.aiab_mode,
                "classifier_verification": getattr(implementation, "verification_metadata", None),
            },
            status_code=200 if runtime.healthy else 503,
        )

    return app
