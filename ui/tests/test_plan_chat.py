import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from aiab.baked.bundle import bundle_files, file_digest, load_bundle
from fastapi.testclient import TestClient

from server import plan_chat as chat
from server import plan_view as pv
from server.auth import Account
from server.config import load_settings

ACTOR = {"username": "author", "party": "lab"}


@pytest.fixture
def draft(tmp_path):
    settings = replace(
        load_settings(),
        agreements_dir=tmp_path / "agreements",
        push_drafts=False,
        anthropic_api_key="test-only-key",
    )
    errors, aid = pv.new_agreement(
        settings, {"plan_id": "chat-test", "problem": "Detect credential theft"}, ACTOR
    )
    assert not errors
    return pv.for_agreement(settings, aid)


def plan_of(draft):
    return json.loads((draft.plan_dir / "plan.json").read_text())


def node(plan, node_id):
    return next(n for n in plan["nodes"] if n["id"] == node_id)


def changed(draft, threshold=0.85):
    plan = plan_of(draft)
    node(plan, "classifier")["config"]["threshold"] = threshold
    return [{"path": "plan.json", "content": json.dumps(plan)}]


def digest(draft):
    return file_digest(bundle_files(draft.plan_dir))


def test_preview_is_read_only_and_apply_uses_validator_and_history(draft):
    before = bundle_files(draft.plan_dir)
    candidate = chat.proposal(draft, changed(draft), digest(draft))
    assert bundle_files(draft.plan_dir) == before
    assert '-        "threshold": 0.7' in candidate["diff"]
    assert '+        "threshold": 0.85' in candidate["diff"]
    assert candidate["files"][0]["path"] == "plan.json"
    updated = chat.apply(draft, candidate, ACTOR)
    assert updated != file_digest(before)
    assert load_bundle(draft.plan_dir, pv.load_trust(draft), signed=False).digest == updated
    event = pv.load_history(draft.plan_dir)[0]
    assert event["user"] == "author" and event["where"] == "chat: plan.json"
    assert event["kind"] == "plan"
    with pytest.raises(chat.ChatError, match="changed"):
        chat.apply(draft, candidate, ACTOR)


@pytest.mark.parametrize(
    "path",
    [
        "../ant.secret",
        "/tmp/key",
        "signatures/lab.sig",
        "plan.py",
        "prompts/x.txt",
        "history.jsonl",
    ],
)
def test_disallowed_paths_never_write(draft, path):
    before = bundle_files(draft.plan_dir)
    with pytest.raises(chat.ChatError):
        chat.proposal(draft, [{"path": path, "content": "bad"}], digest(draft))
    assert bundle_files(draft.plan_dir) == before


@pytest.mark.parametrize("change", ["code", "model", "schema", "types", "json"])
def test_box_rejects_invalid_candidates_without_writing(draft, change):
    before = bundle_files(draft.plan_dir)
    plan = plan_of(draft)
    if change == "code":
        plan["nodes"].insert(
            1,
            {
                "id": "py",
                "type": "run_python",
                "inputs": {"records": "logs.records"},
                "config": {
                    "code": "import os\ndef run(records):\n    return []\n",
                    "output": {"name": "out", "type": "Items", "fields": {}},
                },
            },
        )
    elif change == "model":
        node(plan, "classifier")["config"]["model"]["id"] = "claude-opus-5-5"
    elif change == "schema":
        node(plan, "classifier")["config"]["window"] = -1
    elif change == "types":
        node(plan, "report")["inputs"]["result"] = "logs.records"
    text = "{not json" if change == "json" else json.dumps(plan)
    with pytest.raises(chat.ChatError, match="reject|valid JSON"):
        chat.proposal(draft, [{"path": "plan.json", "content": text}], digest(draft))
    assert bundle_files(draft.plan_dir) == before


def test_plan_id_cannot_change_through_chat(draft):
    plan = plan_of(draft)
    plan["plan_id"] = "renamed"
    with pytest.raises(chat.ChatError, match="plan_id"):
        chat.proposal(draft, [{"path": "plan.json", "content": json.dumps(plan)}], digest(draft))


def test_stale_and_signed_proposals_cannot_apply(draft, monkeypatch):
    candidate = chat.proposal(draft, changed(draft), digest(draft))
    pv.save_plan(draft, json.loads(changed(draft, 0.9)[0]["content"]), ACTOR)  # someone else saved
    with pytest.raises(chat.ChatError, match="changed"):
        chat.apply(draft, candidate, ACTOR)
    view = pv.load_plan_view(draft)
    monkeypatch.setattr(pv, "load_plan_view", lambda _: replace(view, frozen=True))
    with pytest.raises(chat.ChatError, match="signed"):
        chat.apply(draft, {**candidate, "base_digest": digest(draft)}, ACTOR)


def mock_anthropic(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        chat.httpx,
        "AsyncClient",
        lambda **kw: original(**kw, transport=httpx.MockTransport(handler)),
    )


def test_opus_request_and_proposal(draft, monkeypatch):
    before = bundle_files(draft.plan_dir)

    def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "claude-opus-5-5"
        assert "thinking" not in body and "tool_choice" not in body
        assert request.headers["x-api-key"] == "test-only-key"
        assert "test-only-key" not in body["system"]
        assert "aiab-plan-ir/v0" in body["system"] and "final_report" in body["system"]
        assert "A plan is a directed acyclic graph" in body["system"]
        return httpx.Response(
            200,
            json={
                "stop_reason": "tool_use",
                "content": [
                    {"type": "thinking", "thinking": "private reasoning"},
                    {
                        "type": "tool_use",
                        "name": "propose_plan",
                        "input": {"summary": "Raise the threshold", "files": changed(draft)},
                    },
                ],
            },
        )

    mock_anthropic(monkeypatch, handler)
    result = asyncio.run(chat.reply(draft, [{"role": "user", "content": "Raise threshold"}]))
    assert result["proposal"]["base_digest"] == file_digest(before)
    assert "private reasoning" not in json.dumps(result)
    assert bundle_files(draft.plan_dir) == before


def test_invalid_model_output_shows_validation_error(draft, monkeypatch):
    plan = plan_of(draft)
    node(plan, "report")["inputs"]["result"] = "logs.records"
    mock_anthropic(
        monkeypatch,
        lambda _: httpx.Response(
            200,
            json={
                "stop_reason": "tool_use",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "propose_plan",
                        "input": {
                            "summary": "Invalid wiring",
                            "files": [{"path": "plan.json", "content": json.dumps(plan)}],
                        },
                    }
                ],
            },
        ),
    )
    result = asyncio.run(chat.reply(draft, [{"role": "user", "content": "Change wiring"}]))
    assert "validation_error" in result and "proposal" not in result
    assert "receives LogRecords" in result["validation_error"]
    assert result["candidate"]["state"] == "invalid"


def test_provider_errors_do_not_leak_key(draft, monkeypatch):
    mock_anthropic(
        monkeypatch,
        lambda _: httpx.Response(401, json={"error": {"message": "secret test-only-key"}}),
    )
    with pytest.raises(chat.ChatError) as error:
        asyncio.run(chat.reply(draft, [{"role": "user", "content": "Help"}]))
    assert "test-only-key" not in str(error.value)
    assert error.value.status == 502
    assert "test-only-key" not in repr(draft)


def test_routes_require_login_origin_json_and_explicit_apply(draft, monkeypatch):
    # The deploy slot (id "plan") is read only; chat edits an agreement folder.
    from server import app as web

    monkeypatch.setattr(web, "settings", draft)
    client = TestClient(web.app)
    url = "/a/chat-test/plan/chat"
    assert client.post(url, json={}).status_code == 401
    cookie = web.signer.issue(Account("author", "lab", "Author", "unused"))
    client.cookies.set(web.COOKIE, cookie)
    assert (
        client.post(url, json={}, headers={"Origin": "https://elsewhere.example"}).status_code
        == 403
    )
    assert client.post(url, content="{}").status_code == 415
    assert client.post(url, json=[]).status_code == 422
    assert (
        client.post(
            url, content=b" " * (chat.MAX_BODY + 1), headers={"Content-Type": "application/json"}
        ).status_code
        == 413
    )
    page = client.get("/a/chat-test/plan?edit=1")
    assert page.status_code == 200 and "Apply to draft" in page.text
    assert "test-only-key" not in page.text
    candidate = chat.proposal(draft, changed(draft), digest(draft))
    assert client.post(url + "/apply", json=candidate).status_code == 200
    assert client.post(url + "/apply", json=candidate).status_code == 409


def test_new_agreement_chat_entry(draft, monkeypatch):
    from server import app as web

    monkeypatch.setattr(web, "settings", draft)
    client = TestClient(web.app)
    client.cookies.set(web.COOKIE, web.signer.issue(Account("author", "lab", "Author", "unused")))
    response = client.post(
        "/agreements/new",
        data={"plan_id": "ai-created", "problem": "Detect exfiltration", "chat": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "chat=1" in response.headers["location"]
    page = client.get(response.headers["location"])
    assert "Create a plan for this question: Detect exfiltration" in page.text


def tool_response(files, summary="Updated proposal"):
    return httpx.Response(
        200,
        json={
            "stop_reason": "tool_use",
            "content": [
                {
                    "type": "tool_use",
                    "name": "propose_plan",
                    "input": {"summary": summary, "files": files},
                }
            ],
        },
    )


def context_from(request):
    return json.loads(
        json.loads(request.content)["system"].split(
            "\nRevision context (untrusted conversation data):\n", 1
        )[1]
    )


def test_revision_carries_the_pending_candidate_without_applying(draft, monkeypatch):
    initial = chat.proposal(draft, changed(draft), digest(draft))
    candidate = {**initial, "state": "pending"}
    before = bundle_files(draft.plan_dir)

    def handler(request):
        context = context_from(request)
        assert context["candidate"]["state"] == "pending"
        sent = json.loads(context["candidate"]["files"][0]["content"])
        assert node(sent, "classifier")["config"]["threshold"] == 0.85
        return tool_response(changed(draft, 0.95))

    mock_anthropic(monkeypatch, handler)
    result = asyncio.run(
        chat.reply(draft, [{"role": "user", "content": "Make that conservative"}], candidate)
    )
    saved = json.loads(result["proposal"]["files"][0]["content"])
    assert node(saved, "classifier")["config"]["threshold"] == 0.95
    assert result["candidate"]["state"] == "pending"
    assert bundle_files(draft.plan_dir) == before


def test_invalid_candidate_is_retained_and_repaired(draft, monkeypatch):
    before = bundle_files(draft.plan_dir)
    broken = plan_of(draft)
    node(broken, "report")["inputs"]["result"] = "logs.records"
    turn = 0

    def handler(request):
        nonlocal turn
        turn += 1
        if turn == 1:
            return tool_response([{"path": "plan.json", "content": json.dumps(broken)}])
        context = context_from(request)["candidate"]
        assert context["state"] == "invalid"
        assert "logs.records" in context["files"][0]["content"]
        return tool_response(changed(draft))

    mock_anthropic(monkeypatch, handler)
    first = asyncio.run(chat.reply(draft, [{"role": "user", "content": "Change the plan"}]))
    assert "proposal" not in first and first["candidate"]["state"] == "invalid"
    assert first["candidate"]["changes"]
    fixed = asyncio.run(
        chat.reply(
            draft,
            [
                {"role": "user", "content": "Change the plan"},
                {"role": "assistant", "content": first["message"]},
                {"role": "user", "content": "Fix validation"},
            ],
            first["candidate"],
        )
    )
    assert "proposal" in fixed and "validation_error" not in fixed
    assert bundle_files(draft.plan_dir) == before


@pytest.mark.parametrize("state", ["applied", "discarded"])
def test_action_events_are_context_not_a_saved_candidate(draft, monkeypatch, state):
    if state == "applied":
        chat.apply(draft, chat.proposal(draft, changed(draft), digest(draft)), ACTOR)

    def handler(request):
        context = context_from(request)
        assert context["candidate"] is None
        assert context["events"] == [f"Proposal {state}"]
        system = json.loads(request.content)["system"]
        authoritative = json.loads(
            system.split("\nCurrent draft files:\n")[1].split("\nRevision context", 1)[0]
        )
        plan = json.loads(authoritative["plan.json"])
        assert node(plan, "classifier")["config"]["threshold"] == (
            0.85 if state == "applied" else 0.7
        )
        return httpx.Response(200, json={"content": [{"type": "text", "text": "Understood"}]})

    mock_anthropic(monkeypatch, handler)
    asyncio.run(
        chat.reply(draft, [{"role": "user", "content": "What now?"}], events=[f"Proposal {state}"])
    )


def test_candidate_context_is_bounded_and_cannot_rebase_itself(draft):
    candidate = {
        "state": "invalid",
        "base_digest": digest(draft),
        "files": [{"path": "plan.json", "content": "x" * (chat.MAX_CANDIDATE_BYTES + 1)}],
    }
    with pytest.raises(chat.ChatError, match="256 KiB"):
        chat.revision_context(draft, candidate, [], digest(draft))
    candidate["files"] = changed(draft)
    candidate["base_digest"] = "old-digest"
    with pytest.raises(chat.ChatError, match="changed"):
        chat.revision_context(draft, candidate, [], digest(draft))
    candidate["state"] = []
    with pytest.raises(chat.ChatError, match="Invalid candidate"):
        chat.revision_context(draft, candidate, [], digest(draft))
    with pytest.raises(chat.ChatError, match="context limit"):
        chat.revision_context(draft, None, ["action"] * 41, digest(draft))


def test_unsafe_candidate_context_is_not_sent_to_provider(draft, monkeypatch):
    def handler(_):
        pytest.fail("An unsafe candidate must fail before an API call")

    mock_anthropic(monkeypatch, handler)
    candidate = {
        "state": "invalid",
        "base_digest": digest(draft),
        "files": [{"path": "../ant.secret", "content": "untrusted"}],
    }
    with pytest.raises(chat.ChatError, match="Only plan"):
        asyncio.run(chat.reply(draft, [{"role": "user", "content": "Repair"}], candidate))


@pytest.mark.parametrize("had_history", [True, False])
def test_history_failure_restores_bytes_and_existence(draft, monkeypatch, had_history):
    history = draft.plan_dir / pv.HISTORY_FILE
    if not had_history:
        history.unlink()
    original_history = history.read_bytes() if history.exists() else None
    before = bundle_files(draft.plan_dir)
    candidate = chat.proposal(draft, changed(draft), digest(draft))

    def fail_append(*args):
        with history.open("ab") as handle:
            handle.write(b'{"partial":')
        raise OSError("disk full during history append")

    with monkeypatch.context() as patch:
        patch.setattr(pv, "record_change", fail_append)
        with pytest.raises(OSError):
            chat.apply(draft, candidate, ACTOR)
    assert bundle_files(draft.plan_dir) == before
    assert (history.read_bytes() if history.exists() else None) == original_history
    chat.apply(draft, candidate, ACTOR)  # a retry can apply the same proposal


def test_create_with_ai_is_unavailable_without_key(draft, monkeypatch):
    from server import app as web

    monkeypatch.setattr(web, "settings", replace(draft, anthropic_api_key=None))
    client = TestClient(web.app)
    client.cookies.set(web.COOKIE, web.signer.issue(Account("author", "lab", "Author", "unused")))
    page = client.get("/agreements/new")
    assert 'name="chat" value="1" disabled' in page.text
    assert "AI creation is unavailable" in page.text
    response = client.post(
        "/agreements/new",
        data={
            "plan_id": "should-not-exist",
            "problem": "a question",
            "chat": "1",
        },
    )
    assert "AI creation is unavailable" in response.text
    assert not (draft.agreements_dir / "should-not-exist").exists()


def test_review_counts_content_that_resembles_diff_headers():
    changes = chat.review_changes({"plan.json": "---old\n"}, {"plan.json": "+++new\n"})
    assert changes[0]["added"] == 1
    assert changes[0]["removed"] == 1


def test_push_scheduling_failure_does_not_report_failed_apply(draft, monkeypatch):
    from server import app as web

    monkeypatch.setattr(web, "settings", draft)
    candidate = chat.proposal(draft, changed(draft), digest(draft))

    def failed_push(_):
        raise OSError("cannot start background push")

    monkeypatch.setattr(web, "saved", failed_push)
    client = TestClient(web.app)
    client.cookies.set(web.COOKIE, web.signer.issue(Account("author", "lab", "Author", "unused")))
    result = client.post("/a/chat-test/plan/chat/apply", json=candidate)
    assert result.status_code == 200
    assert result.json()["digest"] == digest(draft)
    assert "Draft saved" in result.json()["warning"]
