import json
from dataclasses import replace

from jinja2 import Environment, FileSystemLoader

from server.config import UI_ROOT, load_settings
from server.runs import load_run, outcome


def test_old_verified_failure_is_not_an_all_clear(tmp_path):
    run_id = "20261026T093728Z-aa5894"
    folder = tmp_path / run_id
    folder.mkdir()
    state = {
        "id": run_id,
        "started": "2026-10-26T09:37:28Z",
        "transcript": "demo",
        "by": {"user": "lab", "party": "lab"},
        "status": "done",
        "result": {"flagged": None, "status": "failed", "run_id": "box-run"},
        "steps": [
            {
                "key": "report",
                "title": "Report",
                "status": "done",
                "output": {"report": {"status": "failed", "error": "execution_failed"}},
            },
            {"key": "offline", "title": "Verify", "status": "done"},
        ],
    }
    (folder / "state.json").write_text(json.dumps(state))
    run = load_run(replace(load_settings(), runs_dir=tmp_path), run_id)
    assert run["outcome"]["verified"]
    env = Environment(loader=FileSystemLoader(UI_ROOT / "server/templates"))
    env.filters["pretty_json"] = str
    html = env.get_template("run.html").render(run=run)
    assert "Execution failed — no verdict" in html
    assert "not flagged" not in html.lower()
    assert "Finished and verified" not in html
    assert "execution_failed" in html
    assert "Report verification: passed" in html


def test_verified_false_requires_completed_execution():
    state = {
        "status": "done",
        "steps": [
            {
                "key": "report",
                "output": {"report": {"status": "completed", "result": {"flagged": False}}},
            },
            {"key": "offline", "status": "done"},
        ],
    }
    assert outcome(state)["title"] == "Not flagged"
    state["steps"][1]["status"] = "failed"
    state["status"] = "failed"
    assert outcome(state)["title"] == "Run stopped — no verified verdict"
