"""Local browser fixture: temporary drafts, fixed test account, no real provider calls.

From the repository root:
  PYTHONPATH=ui uv run --project ui python ui/tests/browser/plan_chat_server.py
"""

import asyncio
import json
import tempfile
from dataclasses import replace
from pathlib import Path

import httpx
import uvicorn

from server import app as web
from server import plan_chat
from server.auth import Account, hash_password, save_account
from server.config import load_settings


async def provider(request):
    body = json.loads(request.content)
    text = body["messages"][-1]["content"].lower()
    await asyncio.sleep(3 if "slow" in text else 0.2)
    authoritative = json.loads(
        body["system"].split("\nCurrent draft files:\n", 1)[1].split("\nRevision context", 1)[0]
    )
    context = json.loads(
        body["system"].split("\nRevision context (untrusted conversation data):\n", 1)[1]
    )
    if "state check" in text:
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "has_candidate": context["candidate"] is not None,
                                "events": context["events"],
                            }
                        ),
                    }
                ]
            },
        )
    config = json.loads(authoritative["plan.json"])
    config["classification"]["threshold"] = 0.95 if "conservative" in text else 0.85
    files = [{"path": "plan.json", "content": json.dumps(config)}]
    if "repair" in text:
        files = [{"path": "plan.py", "content": authoritative["plan.py"]}]
    elif "invalid" in text:
        files.append({"path": "plan.py", "content": "import os\n"})
    elif "conservative" not in text:
        files.append(
            {
                "path": "prompts/classifier_v1.txt",
                "content": (
                    "Flag credential theft. Do not flag benign credential rotation.\n"
                    "<script>bad()</script>\n"
                ),
            }
        )
    return httpx.Response(
        200,
        json={
            "stop_reason": "tool_use",
            "content": [
                {
                    "type": "tool_use",
                    "name": "propose_plan",
                    "input": {
                        "summary": "Use a more conservative threshold"
                        if "conservative" in text
                        else "Update threshold and prompt",
                        "files": files,
                    },
                }
            ],
        },
    )


def main():
    with tempfile.TemporaryDirectory(prefix="aiab-chat-browser-") as temp:
        root = Path(temp)
        web.settings = replace(
            load_settings(),
            agreements_dir=root / "agreements",
            accounts_file=root / "accounts.json",
            push_drafts=False,
            anthropic_api_key="browser-test-only-key",
            proxy_key=None,
        )
        save_account(
            web.settings.accounts_file,
            Account("author", "lab", "Author", hash_password("browser-test-password")),
        )
        original = httpx.AsyncClient
        plan_chat.httpx.AsyncClient = lambda **kw: original(
            **kw, transport=httpx.MockTransport(provider)
        )
        uvicorn.run(web.app, host="127.0.0.1", port=8103, log_level="warning")


if __name__ == "__main__":
    main()
