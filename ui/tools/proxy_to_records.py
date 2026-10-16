#!/usr/bin/env python3
"""Turn a test-proxy log segment into box records (the plan's transcript format).

The logging proxy writes one JSON line per request/reply pair (see
context/receiving-test-logs.md). The box and the plan work on LogRecords instead:
stream, batch, offset, agent_id, session_id, ts, kind, content. This script maps one
to the other so a segment can be used as a plan transcript:

  python3 tools/proxy_to_records.py llmproxy-test-<logid>-<first>-<last>.jsonl.zst > out.jsonl

Mapping, one proxy line -> up to four records:
  stream      the <logid> from the file name
  batch       the <first> record number from the file name
  offset      a running number
  agent_id    the proxy's "who" (everyone is "test" on the test proxy today)
  session_id  the request's metadata.user_id when present (Claude Code sends one per
              session), otherwise "<who>-<n>" where n advances after a gap of 30 minutes
  ts          the proxy's "time", as an ISO timestamp
  kind        "request" for the last user message, "tool_result" for tool results in it,
              "response" for the reply's text, "tool_call" for tool_use blocks in it
  content     the text of that part

Only /v1/messages lines are converted. Files ending in .zst are decompressed with the
zstandard module if installed, otherwise with the zstd command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

NAME = re.compile(
    r"^llmproxy-[a-z]+-(?P<logid>[A-Za-z0-9_-]+)-(?P<first>\d+)-(?P<last>\d+)\.jsonl(\.zst)?$"
)
SESSION_GAP_SECONDS = 30 * 60


def read_lines(path: Path) -> list[str]:
    raw = path.read_bytes()
    if path.suffix == ".zst":
        try:
            import zstandard  # type: ignore

            raw = zstandard.ZstdDecompressor().stream_reader(raw).read()
        except ImportError:
            done = subprocess.run(["zstd", "-dc", str(path)], check=True, capture_output=True)
            raw = done.stdout
    return [line for line in raw.decode("utf-8").splitlines() if line.strip()]


def body(part: dict) -> dict | None:
    """The request_raw / response_raw payload as JSON, or None if it is not JSON."""
    text = part.get("text") if isinstance(part, dict) else None
    if text is None:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def blocks(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def record(
    stream: str, batch: int, offset: int, who: str, session: str, ts: str, kind: str, text: str
) -> dict:
    return {
        "stream": stream,
        "batch": batch,
        "offset": offset,
        "agent_id": who,
        "session_id": session,
        "ts": ts,
        "kind": kind,
        "content": text[:1000000],
    }


def parts_of(request: dict, response: dict) -> list[tuple[str, str]]:
    """(kind, text) pairs from the last user message and the reply, in order."""
    found: list[tuple[str, str]] = []
    messages = request.get("messages") or []
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    if last_user:
        for block in blocks(last_user.get("content")):
            if block.get("type") == "text":
                found.append(("request", block.get("text", "")))
            elif block.get("type") == "tool_result":
                found.append(("tool_result", json.dumps(block.get("content"), ensure_ascii=False)))
    for block in blocks(response.get("content")):
        if block.get("type") == "text":
            found.append(("response", block.get("text", "")))
        elif block.get("type") == "tool_use":
            call = json.dumps(block.get("input"), ensure_ascii=False)
            found.append(("tool_call", f"{block.get('name')} {call}"))
    return [(kind, text) for kind, text in found if text.strip()]


def convert(lines: list[str], stream: str, batch: int) -> list[dict]:
    records: list[dict] = []
    last_time: dict[str, float] = {}
    session_n: dict[str, int] = {}
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("path") != "/v1/messages":
            continue
        who = str(entry.get("who") or "unknown")
        when = float(entry.get("time") or 0)
        request = body(entry.get("request_raw")) or {}
        response = body(entry.get("response_raw")) or {}
        user_id = (request.get("metadata") or {}).get("user_id")
        if user_id:
            session = "s-" + hashlib.sha256(str(user_id).encode()).hexdigest()[:12]
        else:
            if when - last_time.get(who, 0) > SESSION_GAP_SECONDS:
                session_n[who] = session_n.get(who, 0) + 1
            session = f"{who}-{session_n.get(who, 1)}"
        last_time[who] = when
        stamp = datetime.fromtimestamp(when, UTC).replace(microsecond=0).isoformat()
        ts = stamp.replace("+00:00", "Z")
        for kind, text in parts_of(request, response):
            records.append(record(stream, batch, len(records), who, session, ts, kind, text))
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("segment", type=Path, help="llmproxy-...jsonl or .jsonl.zst")
    parser.add_argument("--stream", help="override the stream id (default: from the file name)")
    args = parser.parse_args()
    match = NAME.match(args.segment.name)
    stream = args.stream or (match.group("logid") if match else args.segment.stem.split(".")[0])
    batch = int(match.group("first")) if match else 0
    for record in convert(read_lines(args.segment), stream, batch):
        sys.stdout.write(json.dumps(record, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
