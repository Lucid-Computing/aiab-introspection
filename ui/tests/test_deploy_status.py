import asyncio

from server import deploystatus as ds
from server.config import load_settings


def test_superseded_success_does_not_block_new_agreement():
    state = {
        "pr": {"state": "merged", "base": "production"},
        "check": None,
        "release": {"status": "completed", "conclusion": "success"},
        "box_matches": False,
        "superseded": True,
    }
    assert not ds._busy(state)
    assert "superseded" in ds._summary(state)[0]
    state["superseded"] = False
    assert ds._busy(state)


def test_release_matches_merge_commit(monkeypatch):
    async def get(client, path, params=None, raw=False):
        if path.endswith("/pulls"):
            return [
                {
                    "number": 10,
                    "html_url": "pr/10",
                    "state": "closed",
                    "merged_at": "2026-10-25T06:00:00Z",
                    "merge_commit_sha": "merge-10",
                    "head": {"ref": "plan/bio-abc", "sha": "head-10"},
                    "base": {"ref": "production"},
                }
            ]
        if params.get("head_sha") == "merge-10":
            return {
                "workflow_runs": [
                    {
                        "name": ds.RELEASE_WORKFLOW,
                        "status": "completed",
                        "conclusion": "failure",
                        "html_url": "release-10",
                    }
                ]
            }
        assert params.get("head_sha") == "head-10"
        return {"workflow_runs": []}

    monkeypatch.setattr(ds, "_get", get)
    result = asyncio.run(ds._pull_request(load_settings(), None, lambda ref: True))
    assert result["release"]["url"] == "release-10"
