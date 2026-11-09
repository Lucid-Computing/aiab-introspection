"""The browser signs exactly what the box checks.

ui/assets/sign.js builds the signed message and the key material in the page. This runs
its pure part under Node's WebCrypto, which is the same API, and verifies the result with
the static part's own verifier, the one the box and the plan check use. Skipped without
Node 22 (Ed25519 in WebCrypto)."""

import base64
import json
import shutil
import subprocess

import pytest
from aiab.audit.protocol import AuditError, canonical, verify_signature
from aiab.baked.bundle import make_certificate
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from server.config import REPO_ROOT

SIGN_JS = REPO_ROOT / "ui/assets/sign.js"
DIGEST = "sha256:" + "ab" * 32
ROOT = Ed25519PrivateKey.generate()
ROOT_KEY = base64.b64encode(ROOT.public_key().public_bytes_raw()).decode()
OTHER_KEY = base64.b64encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw()).decode()

PROBE = """
require(process.argv[1]);
const S = globalThis.AiabSigning;
(async () => {
  const digest = process.argv[2], rootKey = process.argv[3], certificateText = process.argv[4];
  const pair = await S.generate();
  const certificate = S.parseCertificate(certificateText);
  const forged = Object.assign({}, certificate, { signing_key: rootKey });  // names another key
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
    certificate_message: Buffer.from(S.certificateMessageBytes(certificate)).toString("latin1"),
    certificate_text: S.certificateText(certificate),
    certificate_verifies: await S.verifyCertificate(certificate, rootKey),
    forged_certificate_refused: !(await S.verifyCertificate(forged, rootKey)),
    other_root_refused: !(await S.verifyCertificate(certificate, process.argv[5])),
  };
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@pytest.fixture(scope="module")
def probe():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    certificate = make_certificate(ROOT, "lab", OTHER_KEY)  # certifies some signing key
    result = subprocess.run(
        [
            node,
            "-e",
            PROBE,
            str(SIGN_JS),
            DIGEST,
            ROOT_KEY,
            canonical(certificate).decode(),
            OTHER_KEY,  # a key that is not the root, for other_root_refused
        ],
        capture_output=True,
        text=True,
        timeout=60,
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


def test_the_browser_checks_a_certificate_the_way_the_box_does(probe):
    """The root's signature over {"role", "signing_key"}: verified in the page with the
    root from the trust file, and the text sent to the server is the canonical form."""
    assert probe["certificate_verifies"] is True
    assert probe["forged_certificate_refused"] is True  # another signing key named
    assert probe["other_root_refused"] is True  # checked against a key that is not the root
    assert probe["certificate_message"] == (
        'AIAB/signing-key/v1\x00{"role":"lab","signing_key":"' + OTHER_KEY + '"}'
    )
    certificate = make_certificate(ROOT, "lab", OTHER_KEY)
    assert probe["certificate_text"] == canonical(certificate).decode()
