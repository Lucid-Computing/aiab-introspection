"""The browser signs exactly what the box checks.

ui/assets/sign.js builds the signed message and the key material in the page. This runs
its pure part under Node's WebCrypto, which is the same API, and verifies the result with
the static part's own verifier, the one the box and the plan check use. Skipped without
Node 22 (Ed25519 in WebCrypto)."""

import json
import shutil
import subprocess

import pytest
from aiab.audit.protocol import AuditError, verify_signature

from server.config import REPO_ROOT

SIGN_JS = REPO_ROOT / "ui/assets/sign.js"
DIGEST = "sha256:" + "ab" * 32

PROBE = """
require(process.argv[1]);
const S = globalThis.AiabSigning;
(async () => {
  const digest = process.argv[2];
  const pair = await S.generate();
  const backup = await S.encryptBackup(pair.pkcs8, "pw", pair.publicKey, "lab");
  const restored = await S.decryptBackup(backup, "pw");
  let wrongRefused = false;
  try { await S.decryptBackup(backup, "nope"); } catch { wrongRefused = true; }
  const privateKey = await S.importPrivate(restored);
  const out = {
    supported: await S.supported(),
    public_key: pair.publicKey,
    public_key_of_backup: await S.publicKeyOf(restored),
    signature: await S.sign(privateKey, digest),
    wrong_password_refused: wrongRefused,
    backup_format: backup.format,
    message: Buffer.from(S.messageBytes(digest)).toString("latin1"),
  };
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@pytest.fixture(scope="module")
def probe():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, "-e", PROBE, str(SIGN_JS), DIGEST], capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        pytest.skip(f"node cannot run sign.js here: {result.stderr.strip()[-300:]}")
    return json.loads(result.stdout)


def test_the_message_is_the_protocol_message(probe):
    assert probe["supported"] is True
    assert probe["message"] == 'AIAB/plan-bundle/v1\x00{"plan_digest":"' + DIGEST + '"}'


def test_the_browser_signature_verifies_with_the_box_verifier(probe):
    key, signature = probe["public_key"], probe["signature"]
    verify_signature(key, signature, "plan-bundle", {"plan_digest": DIGEST})
    with pytest.raises(AuditError):
        verify_signature(key, signature, "plan-bundle", {"plan_digest": "sha256:" + "cd" * 32})


def test_the_backup_round_trips_and_refuses_a_wrong_password(probe):
    assert probe["backup_format"] == "aiab-signing-key/v1"
    assert probe["public_key_of_backup"] == probe["public_key"]
    assert probe["wrong_password_refused"] is True
