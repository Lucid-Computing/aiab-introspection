"""Verify release approval before any application data is sent."""

from types import SimpleNamespace

import httpx
import pytest

from aiab.audit.protocol import AuditError
from aiab.audit.transport import ROUTER_REPO, FixedClient, PinnedConnection, RoutedConnection


def connection(handler, conn=None):
    conn = conn or PinnedConnection("model.tinfoil.dev", "org/model", "sha256:" + "a" * 64)
    doc = SimpleNamespace(
        security_verified=True,
        config_repo=conn.repo,
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


ENCLAVE = "gpt-oss-safeguard-120b-inf7.tinfoil.containers.tinfoil.dev"


def routed(handler):
    return connection(handler, RoutedConnection("inference.tinfoil.sh", "gpt-oss-safeguard-120b"))


async def test_router_answer_must_name_an_enclave_of_the_model():
    conn, _ = routed(
        lambda req: httpx.Response(200, json={"ok": True}, headers={"tinfoil-enclave": ENCLAVE})
    )
    assert await conn.request("POST", "/v1/chat/completions", b"{}", "t") == (200, {"ok": True})
    assert conn.served == {ENCLAVE}
    assert conn.release == "sha256:" + "a" * 64
    await conn.close()


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"tinfoil-enclave": "gpt-oss-120b-inf3.tinfoil.containers.tinfoil.dev"},
        {"tinfoil-enclave": "gpt-oss-safeguard-120b-inf7.attacker.example"},
    ],
)
async def test_router_answer_from_another_model_or_unnamed_is_refused(headers):
    conn, _ = routed(lambda req: httpx.Response(200, json={"ok": True}, headers=headers))
    with pytest.raises(AuditError, match="model_unavailable"):
        await conn.request("POST", "/v1/chat/completions", b"{}", "t")
    assert conn.failed and not conn.served
    await conn.close()


async def test_router_accepts_its_current_verified_release_only_from_its_repo():
    conn, doc = routed(
        lambda req: httpx.Response(200, json={}, headers={"tinfoil-enclave": ENCLAVE})
    )
    assert conn.repo == ROUTER_REPO and conn.release_digest is None
    doc.release_digest = "b" * 64  # any release Sigstore verified for the router repo
    conn.check()
    doc.config_repo = "attacker/router"
    with pytest.raises(AuditError):
        conn.check()
    await conn.close()


async def test_router_without_verified_release_digest_is_refused():
    calls = []
    conn, doc = routed(lambda req: calls.append(req))
    doc.release_digest = ""
    with pytest.raises(AuditError):
        await conn.request("POST", "/v1/chat/completions", b"private", "secret-token")
    assert not calls
    await conn.close()
