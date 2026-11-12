"""Records sealed by the logger open only inside the box, and only for the boot they were
sealed for; everything in between sees metadata and ciphertext."""

import base64

import httpx
import pytest

from aiab.audit.api import create_app
from aiab.audit.protocol import AuditError, clone
from aiab.baked.records import verify
from aiab.baked.runtime import Runtime, verify_report
from aiab.baked.sealing import RecordDecryptor, seal_record
from aiab.dryrun import MockModel
from tests.test_baked_plan import submission  # the setup fixture comes from conftest


def sealed_for(runtime, records):
    key, boot = runtime.identity["encryption_key"], runtime.boot_id
    return [seal_record(r, key, boot) for r in records]


def test_identity_publishes_a_fresh_encryption_key(setup):
    _, trust, _, _, _ = setup
    a, b = Runtime(trust, MockModel()), Runtime(trust, MockModel())
    for runtime in (a, b):
        raw = base64.b64decode(runtime.identity["encryption_key"], validate=True)
        assert len(raw) == 32
        assert runtime.manifest["record_encryption"].startswith("hpke-x25519")
    assert a.identity["encryption_key"] != b.identity["encryption_key"]


def test_sealed_record_hides_the_text_and_keeps_the_metadata(setup):
    _, trust, _, _, records = setup
    runtime = Runtime(trust, MockModel())
    sealed = sealed_for(runtime, records)
    for plain, s in zip(records, sealed, strict=True):
        assert s["content"] != plain["content"]
        assert plain["content"] not in s["content"]
        assert s["boot_id"] == runtime.boot_id
        for k in ("stream", "batch", "offset", "agent_id", "session_id", "ts", "kind"):
            assert s[k] == plain[k]
    assert runtime.decryptor.unseal(sealed, runtime.boot_id) == records


async def test_sealed_submission_runs_like_a_plaintext_one(setup):
    _, trust, _, bundle, records = setup
    runtime = Runtime(trust, MockModel())
    data = submission(setup, records=sealed_for(runtime, records))
    tokens = {role: role + "-" + "x" * 40 for role in ("lab", "auditor", "logger")}
    app = create_app(runtime, tokens)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"authorization": "Bearer " + tokens["lab"]},
    ) as client:
        response = await client.post("/aiab/runs", json=data)
        assert response.status_code == 202, response.text
        run_id = response.json()["run_id"]
        await runtime.runs[run_id].task
        envelope = (await client.get(f"/aiab/runs/{run_id}/report")).json()
    # The commitment is over the records as signed by the logger: the sealed ones.
    commitment = verify(data, bundle.config, trust["logger_key"])
    result = verify_report(
        envelope,
        runtime.identity,
        bundle,
        runtime.manifest,
        run_id=run_id,
        input_commitment=commitment,
    )
    assert result["result"] == {"flagged": False, "destructive_sessions": 0, "sessions": 1}
    await runtime.close()


def test_records_sealed_for_an_earlier_boot_are_stale(setup):
    _, trust, _, _, records = setup
    earlier, runtime = Runtime(trust, MockModel()), Runtime(trust, MockModel())
    data = submission(setup, records=sealed_for(earlier, records))
    with pytest.raises(AuditError, match="stale_key") as info:
        runtime.submit(data, "lab")
    assert info.value.status == 409


@pytest.mark.parametrize("attack", ["ciphertext", "moved", "other_key", "half_sealed"])
def test_a_record_that_does_not_open_is_an_invalid_submission(setup, attack):
    _, trust, _, _, records = setup
    runtime = Runtime(trust, MockModel())
    sealed = sealed_for(runtime, records)
    if attack == "ciphertext":
        raw = bytearray(base64.b64decode(sealed[0]["content"]))
        raw[-1] ^= 1
        sealed[0]["content"] = base64.b64encode(bytes(raw)).decode()
    elif attack == "moved":
        sealed[0]["offset"] += 1  # the ciphertext is bound to the record's position
    elif attack == "other_key":
        sealed = sealed_for(Runtime(trust, MockModel()), records)
        for s in sealed:
            s["boot_id"] = runtime.boot_id  # claims this boot, sealed to another box's key
    else:
        del sealed[0]["enc"]
    with pytest.raises(AuditError, match="invalid_submission"):
        runtime.decryptor.unseal(sealed, runtime.boot_id)


def test_plaintext_records_pass_through_unchanged(setup):
    _, trust, _, _, records = setup
    runtime = Runtime(trust, MockModel())
    assert runtime.decryptor.unseal(clone(records), runtime.boot_id) == records


async def test_the_plan_sees_the_text_and_nothing_else_leaves(setup, monkeypatch):
    """Independent of the worker sandbox (Linux only): what submit hands to execute is the
    plaintext, while the submission the box received stays sealed."""
    _, trust, _, _, records = setup
    runtime = Runtime(trust, MockModel())
    seen = {}

    async def fake_execute(bundle, input_records, model):
        seen["records"] = input_records
        raise AuditError("execution_failed")

    monkeypatch.setattr("aiab.baked.runtime.execute", fake_execute)
    data = submission(setup, records=sealed_for(runtime, records))
    run = runtime.submit(data, "lab")
    await run.task
    assert seen["records"] == records
    assert all("enc" in r for r in data["records"])  # the sealed input was not altered
    await runtime.close()


def test_decryptor_exposes_only_the_public_key():
    decryptor = RecordDecryptor()
    public = {k for k in vars(decryptor) if not k.startswith("_")}
    assert public == {"public_key"}
    assert len(base64.b64decode(decryptor.public_key, validate=True)) == 32
