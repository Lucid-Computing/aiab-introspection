"""End to end against a deployed UI and its live box: write, sign, run, verify.

Skipped unless AIAB_E2E_URL, AIAB_E2E_USER and AIAB_E2E_PASSWORD are set. The account must
be an auditor, and the server must hold the lab signing key (AIAB_LAB_KEY) and the auditor
root (AIAB_AUDITOR_ROOT_KEY), the prototype one-click setup. Then, over HTTP only:

1. Create an agreement from the minimal template. The server signs it as the lab.
2. Make a signing key in memory, have the server certify it with the auditor root, sign the
   plan digest with it and upload the signature, as the Sign button does in a browser.
3. Start a run on one recorded session from the Dev Agent Logs (test proxy traffic, never
   Prod Agent Logs unless AIAB_E2E_LOGS says so).
4. Wait for the six steps and check that the report verified and gave a verdict.

The browser's own signing code (ui/assets/sign.js) is not exercised here; it is checked
against the same verifier in test_browser_signing.py. On success the agreement is deleted;
the run stays, as runs always do. On failure the agreement is kept and the run URL printed.

    cd ui && AIAB_E2E_URL=https://… AIAB_E2E_USER=… AIAB_E2E_PASSWORD=… \\
        uv run python -m pytest tests/test_e2e_deployed.py -s

Optional: AIAB_E2E_LOGS (log source id, default "dev"), AIAB_E2E_SESSION (a session id;
default the smallest session with records), AIAB_E2E_TIMEOUT (seconds, default 900).
"""

from __future__ import annotations

import base64
import html
import json
import os
import re
import time
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from aiab.audit.protocol import sign_bytes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

httpx = pytest.importorskip("httpx")

URL = os.environ.get("AIAB_E2E_URL", "").rstrip("/")
USER = os.environ.get("AIAB_E2E_USER", "")
PASSWORD = os.environ.get("AIAB_E2E_PASSWORD", "")
LOGS = os.environ.get("AIAB_E2E_LOGS", "dev")
SESSION = os.environ.get("AIAB_E2E_SESSION", "")
TIMEOUT = float(os.environ.get("AIAB_E2E_TIMEOUT", "900"))

pytestmark = pytest.mark.skipif(
    not (URL and USER and PASSWORD),
    reason="set AIAB_E2E_URL, AIAB_E2E_USER and AIAB_E2E_PASSWORD to run against a deployment",
)

QUESTION = (
    "Did any session ask the assistant to delete files, drop databases or run other"
    " destructive commands?"
)


def text_of(page: str) -> str:
    page = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", " ", page)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page))).strip()


def refusal(response) -> str | None:
    """The error a redirect carries (the UI's back(err=...)), or None."""
    query = parse_qs(urlsplit(response.headers.get("location", "")).query)
    return (query.get("err") or [None])[0]


def redirected_to(response, pattern: str) -> str:
    assert response.status_code == 303, (response.status_code, text_of(response.text)[:500])
    location = response.headers["location"]
    assert refusal(response) is None, refusal(response)
    assert re.match(pattern, location), location
    return location


def pick_session(page: str) -> tuple[str, int]:
    """The chosen session id and its record count, from the Runs page's session list."""
    rows = []
    for block in re.findall(r'(?s)<label class="sess([^"]*)">(.*?)</label>', page):
        classes, body = block
        if "sess--refused" in classes:
            continue
        value = re.search(r'name="session" value="([^"]+)"', body)
        count = re.search(r"(\d+) records", text_of(body))
        if value and count:
            rows.append((html.unescape(value.group(1)), int(count.group(1))))
    if SESSION:
        found = [r for r in rows if r[0] == SESSION]
        assert found, f"session {SESSION} is not on the Runs page for logs={LOGS}"
        return found[0]
    with_records = sorted((r for r in rows if r[1] > 0), key=lambda r: r[1])
    assert with_records, f"no recorded session with records in logs={LOGS}"
    return with_records[0]


def test_signed_audit_runs_end_to_end():
    name = "e2e-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    client = httpx.Client(base_url=URL, follow_redirects=False, timeout=60)
    keep = True
    run_url = None
    try:
        # Log in.
        response = client.post("/login", data={"username": USER, "password": PASSWORD})
        assert response.status_code == 303 and "/login" not in response.headers["location"], (
            "login refused"
        )

        # 1. Write: a new agreement from the minimal template (logs, one classifier, report).
        response = client.post(
            "/agreements/new", data={"plan_id": name, "source": "template", "problem": QUESTION}
        )
        redirected_to(response, rf"^/a/{name}/plan\b")
        response = client.post(f"/a/{name}/plan/finish")  # leave edit mode, as a person would
        redirected_to(response, rf"^/a/{name}/plan\b")
        plan = client.get(f"/a/{name}/plan.json").json()
        digest = plan["digest"]
        assert digest.startswith("sha256:") and not plan["issues"] and not plan["frozen"], plan

        # 2. Sign as the auditor: a fresh key, certified by the server's auditor root.
        key = Ed25519PrivateKey.generate()
        public = base64.b64encode(key.public_key().public_bytes_raw()).decode()
        response = client.post(
            "/sign/certificate", json={"party": "auditor", "signing_key": public}
        )
        assert response.status_code == 200, response.text
        certificate = response.json()["certificate"]
        assert certificate["role"] == "auditor" and certificate["signing_key"] == public
        signature = base64.b64encode(
            key.sign(sign_bytes("plan-bundle", {"plan_digest": digest}))
        ).decode()
        response = client.post(
            f"/a/{name}/sign/upload",
            data={
                "party": "auditor",
                "signature": signature,
                "certificate": json.dumps(certificate, separators=(",", ":"), sort_keys=True),
            },
        )
        redirected_to(response, rf"^/a/{name}/")
        plan = client.get(f"/a/{name}/plan.json").json()
        assert plan["frozen"] and plan["digest"] == digest, "the auditor signature did not freeze"

        # 3. Choose the evidence and start the run.
        page = client.get("/runs", params={"agreement": name, "logs": LOGS}).text
        session, records = pick_session(page)
        response = client.post(
            "/runs/start", data={"agreement": name, "logs": LOGS, "session": session}
        )
        run_url = redirected_to(response, r"^/runs/[^/?]+$")
        print(f"\nrun: {URL}{run_url} ({LOGS} session {session}, {records} records)")

        # 4. Wait for the report, then read the outcome as a person would.
        deadline = time.monotonic() + TIMEOUT
        while True:
            page = client.get(run_url).text
            states = re.findall(r'<li class="(waiting|running|done|failed) ', page)
            if "failed" in states or "Interrupted:" in page or "Stopped:" in page:
                pytest.fail(f"run stopped: {text_of(page)[:3000]}")
            if len(states) == 6 and set(states) == {"done"}:
                break
            if time.monotonic() > deadline:
                pytest.fail(f"run not finished after {TIMEOUT:.0f}s: {states}")
            time.sleep(5)
        shown = text_of(page)
        assert "Report verification: passed" in shown, shown[:3000]
        assert re.search(r"\b(Not flagged|Flagged)\b", shown), shown[:3000]
        assert digest[7:19] in shown and "signed by both parties" in shown, shown[:3000]
        print("outcome:", "Not flagged" if "Not flagged" in shown else "Flagged")
        keep = False
    finally:
        if keep:
            print(f"\nkept agreement {name} for inspection; run: {run_url}")
        else:
            response = client.post(f"/a/{name}/delete")
            assert refusal(response) is None, refusal(response)
        client.close()
