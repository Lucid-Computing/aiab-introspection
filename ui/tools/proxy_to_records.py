#!/usr/bin/env python3
"""Turn a test-proxy log segment into box records (the plan's transcript format).

The logging proxy writes one JSON line per request/reply pair (see
context/receiving-test-logs.md). The box and the plan work on LogRecords instead:
stream, batch, offset, agent_id, session_id, ts, kind, content. This script maps one
to the other so a segment can be used as a plan transcript:

  python3 tools/proxy_to_records.py llmproxy-test-<logid>-<first>-<last>.jsonl.zst > out.jsonl

Mapping, one proxy line -> one record per part, the same as the UI's Runs page
(server/proxylogs.py, call_parts):
  stream      the <logid> from the file name
  batch       the <first> record number from the file name
  offset      a running number
  agent_id    the proxy's "who"
  session_id  Claude Code's session id when the request carries one, otherwise
              "<who>-<time>" where a new session starts after a gap of 30 minutes
  ts          the proxy's "time", as an ISO timestamp
  kind        "request" for the newest user turn's text, "tool_result" for the tool
              results in it, "response" for the reply's text (streamed replies included),
              "tool_call" for the tool calls in it
  content     the text of that part, cut at a few thousand characters

Only /v1/messages lines are converted. Files ending in .zst are decompressed with the
zstandard module if installed, otherwise with the zstd command.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.proxylogs import sessions, to_box_records  # noqa: E402

NAME = re.compile(
    r"^llmproxy-[a-z]+-(?P<logid>[A-Za-z0-9_-]+)-(?P<first>\d+)-(?P<last>\d+)\.jsonl(\.zst)?$"
)


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


def convert(lines: list[str], stream: str, batch: int) -> list[dict]:
    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("path") == "/v1/messages":
            entries.append(entry)
    records: list[dict] = []
    for session in sorted(sessions(entries), key=lambda s: s.requests[0].time):
        for record in to_box_records(session.requests):
            records.append({**record, "stream": stream, "batch": batch, "offset": len(records)})
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
