"""Local browser fixture for the Sign screen: one unsigned agreement, a trust file the
browser test may rewrite, a fixed test account, no box and no GitHub.

From the repository root:
  PYTHONPATH=ui uv run --project ui python ui/tests/browser/sign_server.py
Then, in another terminal, node ui/tests/browser/sign.cjs. The trust file the server
checks signatures against is SIGN_TEST_DIR/trust.json (default /tmp/aiab-sign-browser-test),
which the browser script fills with the key the page made.
"""

import os
import shutil
from dataclasses import replace
from pathlib import Path

import uvicorn

from server import app as web
from server import deploystatus
from server.auth import Account, hash_password, save_account
from server.config import REPO_ROOT, load_settings

FIXTURE = REPO_ROOT / "static/tests/fixtures/graph-plan"


def main():
    root = Path(os.environ.get("SIGN_TEST_DIR", "/tmp/aiab-sign-browser-test"))
    shutil.rmtree(root, ignore_errors=True)
    agreements = root / "agreements"
    shutil.copytree(FIXTURE, agreements / "bio", ignore=shutil.ignore_patterns("trust.json"))
    (agreements / "bio/signatures/lab.sig").unlink()
    shutil.copy(FIXTURE / "trust.json", root / "trust.json")
    web.settings = replace(
        load_settings(),
        agreements_dir=agreements,
        accounts_file=root / "accounts.json",
        trust_file=root / "trust.json",
        trust_follows_production=False,
        box_url=None,
        runs_dir=root / "runs",
        logs_dir=root / "logs",
        demo_keys_dir=root / "keys",
        push_drafts=False,
        proxy_key=None,
    )

    async def no_github(_settings):
        return None

    deploystatus.production_trust = no_github
    save_account(
        web.settings.accounts_file,
        Account("signer", "lab", "Signer", hash_password("browser-test-password")),
    )
    uvicorn.run(web.app, host="127.0.0.1", port=8104, log_level="warning")


if __name__ == "__main__":
    main()
