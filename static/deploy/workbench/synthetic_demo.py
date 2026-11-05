"""Local synthetic demonstration. NO hardware attestation, inference, server or deployment.

All generated identity keys and approvals are synthetic TEST fixtures. Never enroll
these keys in a real deployment. Uses the real factory, collector, runner and crypto.
Run with static/.venv/bin/python static/deploy/workbench/synthetic_demo.py.
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent / "tests"))
from test_confidential import (  # noqa: E402
    TestAttestedConnection,
    collect_args,
    finish,
    synthetic_fixture,
)

from aiab.workbench.confidential_client import collect, token  # noqa: E402
from aiab.workbench.governance import GovernanceStore  # noqa: E402


async def main():
    with tempfile.TemporaryDirectory(prefix="aiab-SYNTHETIC-TEST-") as directory:
        s = synthetic_fixture(Path(directory))
        app = s.runtime.app()
        async with app.router.lifespan_context(app):
            admitted = await collect(**collect_args(s, TestAttestedConnection(app)))
            bearer = token("TEST-owner", s.keys["TEST-owner"], s.runtime.profile_digest)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://TEST-runtime"
            ) as client:
                status = await finish(client, admitted["run_id"], bearer)
                if status.json()["status"] != "completed":
                    raise RuntimeError("synthetic run failed")
                response = await client.post(
                    "/v1/runs/" + admitted["run_id"] + "/release",
                    headers={"authorization": "Bearer " + bearer},
                )
                response.raise_for_status()
                package = response.json()["result"]
                GovernanceStore.verify_receipt(
                    package,
                    s.profile["receipt_public_key"],
                    expected={
                        "run_id": admitted["governance_run_id"],
                        "binding_digest": admitted["binding_digest"],
                        "plan_digest": admitted["plan_digest"],
                        "recipient": "TEST-owner",
                    },
                )
                print(
                    json.dumps(
                        {
                            "assurance": "SYNTHETIC TEST ATTESTATION ONLY",
                            "hardware_attestation": "not performed",
                            "live_inference": "not performed",
                            "receipt_verified": True,
                            "projection": package["projection"],
                        },
                        indent=2,
                    )
                )


if __name__ == "__main__":
    asyncio.run(main())
