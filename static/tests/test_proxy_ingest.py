import base64
import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from aiab.proxy_ingest import app_for, decode_segment, durable_store

NAME = "llmproxy-test-logA-0-0.jsonl.zst"
SOURCE = "llm-proxy-test.lucidcompute.com"


def test_concurrent_delivery_and_conflict(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(lambda _: durable_store(tmp_path, SOURCE, NAME, b"one"), range(8))
        )
    assert sorted(code for code, _ in results) == [200] * 7 + [201]
    assert durable_store(tmp_path, SOURCE, NAME, b"two")[0] == 409
    assert (tmp_path / SOURCE / NAME).read_bytes() == b"one"
    assert not list(tmp_path.rglob(".upload-*"))


async def test_receipt_contract(tmp_path):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_for(tmp_path)), base_url="http://test"
    ) as client:
        assert (await client.get("/healthz")).json() == {"ok": True}
        result = await client.put(f"/v1/{SOURCE}/{NAME}", content=b"bytes")
        assert result.status_code == 201
        assert result.json() == {
            "status": "stored",
            "sha256": hashlib.sha256(b"bytes").hexdigest(),
            "bytes": 5,
        }
        assert (await client.put(f"/v1/{SOURCE}/{NAME}", content=b"bytes")).status_code == 200
        assert (await client.put(f"/v1/{SOURCE}/{NAME}", content=b"other")).status_code == 409
        assert (await client.put(f"/v1/unknown/{NAME}", content=b"bytes")).status_code == 400
        assert (await client.put(f"/v1/{SOURCE}/bad", content=b"bytes")).status_code == 400
        assert (await client.put(f"/v1/{SOURCE}/{NAME}", content=b"")).status_code == 413


def segment(tmp_path):
    entry = {
        "id": "req-a",
        "who": "test",
        "time": 1792051200.12,
        "request_raw": {"text": '{"messages":[{"role":"user","content":"hello"}]}'},
        "response_raw": {"base64": base64.b64encode(b'data: {"type":"tool_use"}\n\n').decode()},
    }
    path = tmp_path / NAME
    path.write_bytes(
        subprocess.run(
            ["zstd", "-q", "-c"],
            input=json.dumps(entry).encode() + b"\n",
            capture_output=True,
            check=True,
        ).stdout
    )
    return path


def test_decode_preserves_streaming_and_provenance(tmp_path):
    path = segment(tmp_path)
    value = decode_segment(path, {"req-a": {"agent_id": "test", "session_id": "session-a"}})
    assert value["verified"] is False
    assert value["records"][1]["content"] == 'data: {"type":"tool_use"}\n\n'
    assert [r["offset"] for r in value["records"]] == [0, 1]
    assert value["segment_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_missing_identity_and_range_fail_closed(tmp_path):
    path = segment(tmp_path)
    with pytest.raises(ValueError, match="identity mapping"):
        decode_segment(path, {})
    wrong = path.with_name("llmproxy-test-logA-0-1.jsonl.zst")
    path.rename(wrong)
    with pytest.raises(ValueError, match="line count"):
        decode_segment(wrong, {})
