import asyncio

import pytest

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


@pytest.mark.parametrize(
    "head_sha,title",
    [
        ("later-merge", "Release default · merge-10"),
        ("merge-10", ds.RELEASE_WORKFLOW),
    ],
)
def test_release_matches_approved_source_even_if_dispatch_head_advanced(
    monkeypatch, head_sha, title
):
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
        if path.endswith("/workflows/release.yml/runs"):
            return {
                "workflow_runs": [
                    {
                        "name": ds.RELEASE_WORKFLOW,
                        "head_sha": head_sha,
                        "display_title": title,
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


def test_box_branch_identity_keeps_independent_deployments_separate():
    assert ds._box_for_branch("plan/panda-12345678") == "default"
    assert ds._box_for_branch("plan/box/dev/panda-12345678") == "dev"
    assert ds._box_for_branch("plan/box/staging/bio-12345678") == "staging"
