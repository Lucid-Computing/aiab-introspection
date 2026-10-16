"""Receive unsigned test-proxy segments; never manufacture logger evidence.

Transport receipt is not logger attestation. Keep this service outside the audit
API until the proxy publishes its native signature/chain verification protocol.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from aiab.audit.protocol import validate
from aiab.baked.records import RECORD

SOURCE = "llm-proxy-test.lucidcompute.com"
NAME = re.compile(r"llmproxy-test-([A-Za-z0-9_-]+)-(\d+)-(\d+)\.jsonl\.zst")
MAX_UPLOAD = 16 * 1024 * 1024
MAX_DECODED = 64 * 1024 * 1024


def durable_store(root, source, name, body):
    """Atomic create-if-absent, including concurrent duplicate deliveries."""
    directory = root / source
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / name
    sha = hashlib.sha256(body).hexdigest()
    fd, temporary = tempfile.mkstemp(prefix=".upload-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, target)
            code, status = 201, "stored"
        except FileExistsError:
            held = hashlib.sha256(target.read_bytes()).hexdigest()
            if held != sha:
                return 409, {"status": "different_content", "existing_sha256": held}
            code, status = 200, "already_have"
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return code, {"status": status, "sha256": sha, "bytes": len(body)}
    finally:
        os.unlink(temporary)


def app_for(root, source=SOURCE):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    async def health():
        return {"ok": True}

    @app.put("/v1/{incoming_source}/{name}")
    async def receive(incoming_source: str, name: str, request: Request):
        if incoming_source != source:
            return JSONResponse({"status": "unknown_source"}, status_code=400)
        if len(name) > 200 or not NAME.fullmatch(name):
            return JSONResponse({"status": "bad_name"}, status_code=400)
        try:
            length = int(request.headers.get("content-length", "-1"))
        except ValueError:
            length = -1
        if not 0 < length <= MAX_UPLOAD:
            return JSONResponse({"status": "invalid_length"}, status_code=413)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > length:
                return JSONResponse({"status": "invalid_length"}, status_code=400)
        if len(body) != length:
            return JSONResponse({"status": "invalid_length"}, status_code=400)
        # Storage can block on fsync; keep the ASGI event loop available.
        from starlette.concurrency import run_in_threadpool

        code, response = await run_in_threadpool(durable_store, root, source, name, bytes(body))
        return JSONResponse(response, status_code=code)

    return app


def raw_text(value):
    if set(value) == {"text"} and isinstance(value["text"], str):
        return value["text"]
    if set(value) == {"base64"}:
        # No lossy decoding of arbitrary binary content into classifier text.
        return base64.b64decode(value["base64"], validate=True).decode("utf-8")
    raise ValueError("Unsupported raw body encoding")


def decode_segment(path, mapping):
    """Require explicit request -> agent/session mapping; never infer identities.

    Preserve raw JSON and SSE in content, including tool calls/results. One
    source line produces request and response records with stable offsets.
    """
    match = NAME.fullmatch(path.name)
    if not match or path.stat().st_size > MAX_UPLOAD:
        raise ValueError("Invalid segment name or size")
    log_id, first, last = match.groups()
    first, last = int(first), int(last)
    if first > last:
        raise ValueError("Invalid sequence range")
    # zstd bounds decoder memory; output is streamed with a separate expansion cap.
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            ["zstd", "-q", "-d", "-c", "--memory=64MB", "--", str(path)],
            stdout=subprocess.PIPE,
            stderr=errors,
        )
        try:
            raw = process.stdout.read(MAX_DECODED + 1)
            if len(raw) > MAX_DECODED:
                raise ValueError("Decompressed segment too large")
            if process.wait(timeout=30) != 0:
                raise ValueError("Invalid compressed segment")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
    lines = raw.splitlines()
    if len(lines) != last - first + 1:
        raise ValueError("Record range does not match line count")
    records = []
    for offset, line in enumerate(lines):
        entry = json.loads(line)
        identity = mapping.get(entry["id"])
        if not identity or set(identity) != {"agent_id", "session_id"}:
            raise ValueError("Missing explicit request identity mapping")
        timestamp = datetime.fromtimestamp(entry["time"], UTC).isoformat()
        for index, (field, kind) in enumerate(
            (("request_raw", "request"), ("response_raw", "response"))
        ):
            record = {
                "stream": log_id,
                "batch": first,
                "offset": offset * 2 + index,
                **identity,
                "ts": timestamp,
                "kind": kind,
                "content": raw_text(entry[field]),
            }
            validate(record, RECORD, "invalid_proxy_record")
            records.append(record)
    return {
        "schema": "aiab-unsigned-proxy-import/v1",
        "source": SOURCE,
        "provenance": "unsigned-test-proxy",
        "verified": False,
        "segment": path.name,
        "segment_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "first_sequence": first,
        "last_sequence": last,
        "records": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--root", type=Path, required=True)
    serve.add_argument("--port", type=int, default=8090)
    decode = commands.add_parser("decode")
    decode.add_argument("segment", type=Path)
    decode.add_argument("--mapping", type=Path, required=True)
    decode.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == "serve":
        import uvicorn

        uvicorn.run(app_for(args.root), host="127.0.0.1", port=args.port, access_log=False)
    else:
        value = decode_segment(args.segment, json.loads(args.mapping.read_text()))
        with args.output.open("x") as output:
            json.dump(value, output)
            output.write("\n")
        print("Decoded unsigned segment; no logger proof or completeness claim.")


if __name__ == "__main__":
    main()
