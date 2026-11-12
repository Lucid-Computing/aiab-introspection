"""Every page renders with one agreement on disk and no deploy slot, box or GitHub."""

import json
import shutil
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from server import app as ui
from server import deploystatus
from server.config import REPO_ROOT, load_settings, log_sources_from_env

LAB = {"username": "lab", "party": "lab", "display_name": "Lab"}
FIXTURE = REPO_ROOT / "static/tests/fixtures/graph-plan"


@pytest.fixture
def client(tmp_path, monkeypatch):
    agreements = tmp_path / "agreements"
    shutil.copytree(FIXTURE, agreements / "bio", ignore=shutil.ignore_patterns("trust.json"))
    settings = replace(
        load_settings(),
        agreements_dir=agreements,
        trust_file=FIXTURE / "trust.json",
        trust_follows_production=False,
        box_url=None,
        runs_dir=tmp_path / "runs",
        logs_dir=tmp_path / "logs",
        log_sources=log_sources_from_env({"AIAB_LOG_KEY": "p", "AIAB_PROXY_KEY": "t"}),
        release_file=REPO_ROOT / "static/deploy/tinfoil/audit/release.json",
        demo_keys_dir=tmp_path / "keys",
    )
    monkeypatch.setattr(ui, "settings", settings)
    monkeypatch.setattr(ui, "user_of", lambda request: LAB)

    async def no_github(_settings):
        return None

    monkeypatch.setattr(deploystatus, "production_trust", no_github)
    return TestClient(ui.app, follow_redirects=False)


@pytest.mark.parametrize(
    "path",
    [
        "/how",
        "/agreements",
        "/agreements/new",
        "/runs",
        "/runs?agreement=bio",
        "/a/bio/plan",
        "/a/bio/sign",
    ],
)
def test_page_renders(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.text[-500:]


def test_runs_page_offers_the_signed_agreement_and_names_what_is_missing(client):
    html = client.get("/runs").text
    assert 'value="bio" selected' in html and "Plan to send" in html
    assert "sha256:9b16420a35b9eea517ef88e6715726425d20387967ea40ff7e22f401cc2fcc9e" in html
    assert "lab.token in" in html  # the demo keys are missing, so the run is off


def test_legacy_routes_go_to_agreements(client):
    assert client.get("/plan").headers["location"] == "/agreements"
    assert client.get("/deployed").status_code == 404
    assert client.post("/a/bio/deploy").status_code == 404


def test_run_refuses_without_an_agreement(client):
    response = client.post("/runs/start", data={"session": "x"})
    assert response.status_code == 303 and "Pick%20an%20agreement" in response.headers["location"]


def _record(who, text, session="shared"):
    import json

    return (
        json.dumps(
            {
                "id": f"{who}-1",
                "time": 1_700_000_000.0,
                "who": who,
                "path": "/v1/messages",
                "request_headers": {"x-claude-code-session-id": session},
                "request_raw": {
                    "text": json.dumps(
                        {"model": "m", "messages": [{"role": "user", "content": text}]}
                    )
                },
                "response_raw": {"text": "{}"},
            }
        )
        + "\n"
    )


def test_runs_page_and_run_start_follow_the_chosen_log_source(client, tmp_path, monkeypatch):
    """Prod and dev can hold a session with the same id: the Runs page lists the chosen
    source's sessions and a run started from it sends that source's records."""
    logs = tmp_path / "logs"
    for who in ("prod", "dev"):
        (logs / who).mkdir(parents=True)
        (logs / who / "live.jsonl").write_text(_record(f"{who}-user", f"{who} prompt"))

    page = client.get("/runs?agreement=bio").text  # default: Dev Agent Logs
    assert "from Dev Agent Logs" in page and "from Prod Agent Logs" not in page
    assert 'name="logs" value="dev"' in page
    assert 'class="srcsel"' not in page  # the log is the agreement's choice, not the page's
    page = client.get("/runs?agreement=bio&logs=prod").text
    assert "from Prod Agent Logs" in page and "from Dev Agent Logs" not in page

    started = {}

    async def ready(_folder):
        return {"ok": True, "missing": [], "release": None, "plan_digest": "x"}

    def fake_start(settings, user, agreement, folder, records, label=None):
        started.update(records=records, label=label)
        return "run-1"

    monkeypatch.setattr(ui, "_run_preflight", ready)
    monkeypatch.setattr(ui.runs, "start_run", fake_start)
    monkeypatch.setattr(ui.pv, "record_change", lambda *a, **k: None)
    response = client.post(
        "/runs/start", data={"agreement": "bio", "logs": "prod", "session": "shared"}
    )
    assert response.status_code == 303 and response.headers["location"] == "/runs/run-1"
    assert [r["content"] for r in started["records"]] == ["prod prompt"]
    assert "from Prod Agent Logs" in started["label"]
    response = client.post("/runs/start", data={"agreement": "bio", "session": "shared"})
    assert [r["content"] for r in started["records"]] == ["dev prompt"]
    assert "from Dev Agent Logs" in started["label"]


def test_a_plant_session_is_sent_to_the_box_as_the_plant_wrote_it(client, tmp_path, monkeypatch):
    export = tmp_path / "plant" / "run-1" / "records"
    export.mkdir(parents=True)
    rows = [
        {
            "stream": "proxy",
            "batch": 0,
            "offset": i,
            "agent_id": "agt-1",
            "session_id": "s1",
            "ts": f"2026-01-01T10:0{i}:00Z",
            "kind": kind,
            "content": f"text {i}",
        }
        for i, kind in enumerate(["request", "tool_call", "tool_result", "response"])
    ]
    (export / "s1.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    env = {"AIAB_PROXY_KEY": "t", "AIAB_PLANT_EXPORT": str(tmp_path / "plant")}
    monkeypatch.setattr(ui, "settings", replace(ui.settings, log_sources=log_sources_from_env(env)))
    started = {}

    async def ready(_folder):
        return {"ok": True, "missing": [], "release": None, "plan_digest": "x"}

    def fake_start(settings, user, agreement, folder, records, label=None):
        started.update(records=records, label=label)
        return "run-1"

    monkeypatch.setattr(ui, "_run_preflight", ready)
    monkeypatch.setattr(ui.runs, "start_run", fake_start)
    monkeypatch.setattr(ui.pv, "record_change", lambda *a, **k: None)
    assert "from Plant Logs" in client.get("/runs?agreement=bio&logs=plant").text
    response = client.post(
        "/runs/start", data={"agreement": "bio", "logs": "plant", "session": "s1"}
    )
    assert response.status_code == 303
    assert started["records"] == rows and "from Plant Logs, 4 records" in started["label"]


def test_sign_page_is_two_things_per_party_sign_and_the_root_key(client, tmp_path):
    (tmp_path / "agreements/bio/signatures/lab.sig").unlink()  # the fixture is already signed
    html = client.get("/a/bio/sign").text
    assert '<script src="/assets/sign.js" defer></script>' in html
    assert 'class="browser-key" data-party="lab" data-digest="sha256:' in html
    assert "Set up signing" in html and "Use this key file" in html  # no terminal needed
    assert 'id="key-form-lab"' in html and "Submit root key" in html
    # The card hides buttons with the hidden attribute, which .btn's display would override.
    css = (REPO_ROOT / "ui/assets/aiab.css").read_text()
    assert ".browser-key [hidden] { display: none !important; }" in css
    # No pasted signatures and no commands on the page; the upload route itself stays.
    assert 'id="upload-form-lab"' not in html and "openssl" not in html
    assert "sign_digest.py" not in html
    assert 'data-party="auditor"' not in html  # only your own party's card has the key block


def test_started_run_records_and_links_the_plan_version_it_sent(client, tmp_path, monkeypatch):
    """A flag must be traceable to the wiring that produced it: the run records the digest
    of the copy it sends, and its page links to that copy, read only."""
    monkeypatch.setattr(ui.runs, "_execute", lambda *a: None)  # no CLI, no box
    settings = ui.settings
    digest = ui.runs.preflight(settings, tmp_path / "agreements/bio")["plan_digest"]
    assert digest
    run_id = ui.runs.start_run(
        settings, LAB, "bio", tmp_path / "agreements/bio", [{"content": "hello"}]
    )
    state = json.loads((settings.runs_dir / run_id / "state.json").read_text())
    assert state["plan"] == {
        "agreement": "bio",
        "digest": digest,
        "signed": True,
        "checked": "at start",
    }

    # Editing the agreement afterwards does not change what the run points at.
    plan = tmp_path / "agreements/bio/plan.json"
    plan.write_text(plan.read_text().replace('"title"', '"title" ', 1))

    page = client.get(f"/runs/{run_id}").text
    assert f'href="/runs/{run_id}/plan"' in page
    assert digest[7:19] in page and "signed by both parties" in page
    listing = client.get("/runs").text
    assert f'href="/runs/{run_id}/plan"' in listing

    snapshot = client.get(f"/runs/{run_id}/plan")
    assert snapshot.status_code == 200, snapshot.text[-500:]
    assert digest in snapshot.text and "Read only" in snapshot.text
    assert "/plan/edit" not in snapshot.text and "/sign" not in snapshot.text
    assert "audit-plan-playground" not in snapshot.text
    assert client.get("/runs/20260101T000000Z-abcdef/plan").status_code == 303


def test_past_runs_are_filtered_and_paged(client, tmp_path, monkeypatch):
    """Past runs come 20 to a page, newest first; the search box narrows them and the pager
    keeps the agreement and the filters."""
    monkeypatch.setattr(ui.runs, "_execute", lambda *a: None)  # no CLI, no box
    for i in range(25):
        ui.runs.start_run(
            ui.settings,
            LAB,
            "bio",
            tmp_path / "agreements/bio",
            [{"content": "hello"}],
            label="benign session" if i < 3 else f"session {i}",
        )
    rows = 'class="btn btn--ghost btn--sm" href="/runs/'
    page = client.get("/runs?agreement=bio").text
    assert page.count(rows) == 20 and "page 1 of 2 · 25 runs" in page
    assert 'href="/runs?rpage=2&amp;agreement=bio&amp;logs=' in page
    assert client.get("/runs?agreement=bio&rpage=2").text.count(rows) == 5

    found = client.get("/runs?agreement=bio&rq=BENIGN").text
    assert found.count(rows) == 3 and "logs-pager" not in found
    assert client.get("/runs?rby=nobody").text.count(rows) == 0
    assert "No run matches." in client.get("/runs?rq=zzz").text


def test_sign_is_one_step_from_the_plan_and_the_list_and_the_sign_screen_is_the_fallback(
    client, tmp_path
):
    """Sign on the plan and agreements pages signs in the browser when its key is trusted;
    only a browser that is not set up lands on the Sign screen, told what is missing."""
    (tmp_path / "agreements/bio/signatures/lab.sig").unlink()  # the fixture is already signed
    plan = client.get("/a/bio/plan").text
    assert '<script src="/assets/sign.js" defer></script>' in plan
    assert 'quick-sign" href="/a/bio/sign" data-party="lab" data-digest="sha256:' in plan
    assert 'data-upload="/a/bio/sign/upload" data-next="/a/bio/plan"' in plan
    assert "Waiting for the lab · Signed by the auditor." in plan
    assert 'href="/a/bio/sign">Keys and signatures</a>' in plan
    listing = client.get("/agreements").text
    assert '<script src="/assets/sign.js" defer></script>' in listing
    assert 'quick-sign" href="/a/bio/sign" data-party="lab"' in listing
    assert 'data-upload="/a/bio/sign/upload" data-next="/agreements"' in listing
    assert "Waiting for the lab · Signed by the auditor" in listing
    assert 'disabled title="needs both signatures">Run' in listing
    assert '<code class="hash" title="sha256:' in listing
    fallback = client.get("/a/bio/sign?why=none").text
    assert "Signing is not set up in this browser yet" in fallback
    assert "cannot make Ed25519 keys" in client.get("/a/bio/sign?why=unsupported").text


def test_a_signed_plan_offers_re_sign_and_signature_actions_return_to_the_button_page(
    client,
):
    plan = client.get("/a/bio/plan").text
    assert 'action="/a/bio/sign/remove"' in plan and 'name="next" value="/a/bio/plan"' in plan
    assert "quick-sign" not in plan and "Signed by the lab · Signed by the auditor." in plan
    assert 'title="You signed this version">Signed</a>' in client.get("/agreements").text

    removed = client.post("/a/bio/sign/remove", data={"party": "lab", "next": "/a/bio/plan"})
    assert removed.status_code == 303
    assert removed.headers["location"].startswith("/a/bio/plan?msg=")
    # A problem always shows on the Sign screen, whatever page the button was on.
    bad = client.post(
        "/a/bio/sign/upload",
        data={"party": "lab", "signature": "not a signature", "next": "/a/bio/plan"},
    )
    assert bad.headers["location"].startswith("/a/bio/sign?err=")
    # Only a path on this site is a return target.
    assert ui._sign_next("bio", "https://example.com/") == "/a/bio/sign"
    assert ui._sign_next("bio", "//example.com/") == "/a/bio/sign"
    assert ui._sign_next("bio", "") == "/a/bio/sign"


def test_in_edit_mode_sign_goes_to_the_sign_screen_because_the_builder_saves_in_its_frame(
    client, tmp_path
):
    (tmp_path / "agreements/bio/signatures/lab.sig").unlink()
    assert client.post("/a/bio/plan/edit", data={"next": "/a/bio/plan"}).status_code == 303
    plan = client.get("/a/bio/plan?edit=1").text
    assert "quick-sign" not in plan and 'href="/a/bio/sign">Sign</a>' in plan
    ui.editing.clear("bio")


def test_a_new_agreement_can_copy_another_plan(client, tmp_path):
    """ "Copy from" takes the plan only: the nodes as they were, a new name, no auditor
    signature and a history that starts with the copy."""
    bio = json.loads((tmp_path / "agreements/bio/plan.json").read_text())
    page = client.get("/agreements/new").text
    assert 'name="copy_from"' in page and '<option value="bio"' in page
    response = client.post(
        "/agreements/new",
        data={
            "plan_id": "bio-copy",
            "problem": "same question",
            "source": "copy",
            "copy_from": "bio",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text[-300:]
    copy = json.loads((tmp_path / "agreements/bio-copy/plan.json").read_text())
    assert copy["nodes"] == bio["nodes"] and copy["plan_id"] == "bio-copy"
    assert copy["description"] == "same question"
    assert not (tmp_path / "agreements/bio-copy/signatures/auditor.sig").exists()
    assert "copied from bio" in (tmp_path / "agreements/bio-copy/history.jsonl").read_text()
    refused = client.post(
        "/agreements/new",
        data={"plan_id": "x2", "problem": "q", "source": "copy", "copy_from": "nope"},
    )
    assert "copy from: pick an existing agreement" in refused.text


def test_a_run_reads_the_log_source_the_input_filter_names(client, tmp_path, monkeypatch):
    """When the agreement's Input filter names one of the server's log sources, the Runs page
    offers no selector and a run reads that source, whatever the form says."""
    import server.app as ui

    plan_file = tmp_path / "agreements/bio/plan.json"
    plan = json.loads(plan_file.read_text())
    next(n for n in plan["nodes"] if n["type"] == "input_logs")["config"]["source"] = "prod"
    plan_file.write_text(json.dumps(plan))
    logs = tmp_path / "logs"
    for who in ("prod", "dev"):
        (logs / who).mkdir(parents=True, exist_ok=True)
        (logs / who / "live.jsonl").write_text(_record(f"{who}-user", f"{who} prompt"))
    # The edit above broke the signatures; the summary bar shows only for a runnable agreement.
    monkeypatch.setattr(ui, "_runnable", lambda rows: rows)
    page = client.get("/runs?agreement=bio&logs=dev").text
    assert "from Prod Agent Logs, as the agreement" in page and 'class="srcsel"' not in page
    started = {}

    async def ready(_folder):
        return {"ok": True, "missing": [], "release": None, "plan_digest": "x"}

    def fake_start(settings, user, agreement, folder, records, label=None):
        started.update(records=records, label=label)
        return "run-1"

    monkeypatch.setattr(ui, "_run_preflight", ready)
    monkeypatch.setattr(ui.runs, "start_run", fake_start)
    monkeypatch.setattr(ui.pv, "record_change", lambda *a, **k: None)
    client.post("/runs/start", data={"agreement": "bio", "logs": "dev", "session": "shared"})
    assert [r["content"] for r in started["records"]] == ["prod prompt"]


def test_the_input_filter_can_name_sessions_and_the_runs_page_follows(client, tmp_path):
    """Sessions named in the agreement's Input filter are the only ones a run takes; the
    builder reads the list of sessions from /logs/sessions.json."""
    logs = tmp_path / "logs" / "dev"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "live.jsonl").write_text(_record("dev-user", "dev prompt"))
    listing = client.get("/logs/sessions.json?logs=dev").json()
    assert listing["source"] == "dev" and len(listing["sessions"]) == 1
    session = listing["sessions"][0]
    assert session["records"] >= 1 and "prompt" not in json.dumps(listing)

    plan_file = tmp_path / "agreements/bio/plan.json"
    plan = json.loads(plan_file.read_text())
    source = next(n for n in plan["nodes"] if n["type"] == "input_logs")
    source["config"]["filter"]["sessions"] = ["some-other-session"]
    plan_file.write_text(json.dumps(plan))
    import server.runs as runs_module

    assert runs_module.plan_sessions(tmp_path / "agreements/bio") == {"some-other-session"}
