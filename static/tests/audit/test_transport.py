"""Verify release approval before any application data is sent."""

from types import SimpleNamespace

import httpx
import pytest

from aiab.audit.protocol import AuditError
from aiab.audit.transport import FixedClient, PinnedConnection


def connection(handler):
    conn = PinnedConnection("model.tinfoil.dev", "org/model", "sha256:" + "a" * 64)
    doc = SimpleNamespace(
        security_verified=True,
        config_repo="org/model",
        release_digest="a" * 64,
        enclave_host=conn.host,
        steps={
            name: SimpleNamespace(status="success")
            for name in ("fetch_digest", "verify_code", "verify_enclave", "compare_measurements")
        },
    )
    conn.secure = SimpleNamespace(
        enclave=conn.host,
        get_verification_document=lambda: doc,
        _ground_truth=SimpleNamespace(public_key="key-one"),
    )
    conn.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return conn, doc


@pytest.mark.parametrize(
    "field,value",
    [
        ("security_verified", False),
        ("release_digest", "b" * 64),
        ("config_repo", "attacker/model"),
        ("enclave_host", "other.tinfoil.dev"),
    ],
)
async def test_reject_before_sending_secret(field, value):
    calls = []
    conn, doc = connection(lambda req: calls.append(req))
    setattr(doc, field, value)
    with pytest.raises(AuditError, match="model_unavailable"):
        await conn.request("POST", "/v1/chat/completions", b"private", "secret-token")
    assert not calls
    await conn.close()


async def test_key_rotation_is_latched_closed():
    conn, _ = connection(lambda req: httpx.Response(200, json={"ok": True}))
    conn.check()
    conn.secure._ground_truth.public_key = "key-two"
    with pytest.raises(AuditError):
        await conn.request("GET", "/health")
    conn.secure._ground_truth.public_key = "key-one"
    with pytest.raises(AuditError):
        conn.check()
    await conn.close()


async def test_redirects_are_never_followed():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(307, headers={"location": "https://attacker.example"})

    conn, _ = connection(handler)
    with pytest.raises(AuditError):
        await conn.request("POST", "/v1/chat/completions", b"private", "secret-token")
    assert len(calls) == 1
    await conn.close()


async def test_sdk_cannot_reattest_and_resend_without_new_approval():
    client = object.__new__(FixedClient)
    with pytest.raises(AuditError):
        client._rebuild_sync_transport()
    with pytest.raises(AuditError):
        await client._rebuild_async_transport()
