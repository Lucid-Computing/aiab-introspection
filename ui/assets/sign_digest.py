#!/usr/bin/env python3
"""Your side of signing an AIAB agreement, in one small tool. Your private key never leaves
this machine.

Needs Python 3.10+ and the "cryptography" package (pip install cryptography).

  python3 sign_digest.py keygen --out ~/lab-root.key
      makes a key pair; prints the public key. Used once for your root (the key the box
      is built with, submitted on the Sign screen) and once per signing key.

  python3 sign_digest.py pubkey --key ~/lab.key
      prints a key file's public key (keygen printed it once already)

  python3 sign_digest.py certify --root ~/lab-root.key --role lab --signing-key <public key>
      the certificate your root gives a signing key (the one the Sign screen made in
      your browser, or one from keygen): a JSON line to paste into the Sign screen under
      Register the certificate, or to keep next to a key you sign with here

  python3 sign_digest.py sign --digest sha256:... --key ~/lab.key --certificate ~/lab.cert \\
      --party lab --user teddy --submit http://127.0.0.1:8090/a/<agreement>/sign/upload
      signs the digest and submits the signature and the certificate to the UI (asks for
      your UI password); without --submit it just prints the signature to paste

  python3 sign_digest.py digest plan.zip
      prints the digest of a downloaded plan bundle, to compare with the website

A key file holds one line: the base64 of a raw 32-byte Ed25519 private key.
The signed bytes are exactly what the box verifies:
  "AIAB/plan-bundle/v1\\0" + '{"plan_digest":"sha256:..."}'      for a plan
  "AIAB/signing-key/v1\\0" + '{"role":"lab","signing_key":"..."}'  for a certificate
"""

import argparse
import base64
import getpass
import hashlib
import os
import re
import sys
import zipfile


def folder_digest(files: dict[str, bytes]) -> str:
    hashed = hashlib.sha256(b"AIAB/folder/v1\0")
    for name, raw in sorted(files.items()):
        path = name.encode()
        hashed.update(len(path).to_bytes(4, "big") + path + len(raw).to_bytes(8, "big") + raw)
    return "sha256:" + hashed.hexdigest()


def keygen(out: str) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    fd = os.open(os.path.expanduser(out), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(base64.b64encode(key.private_bytes_raw()).decode() + "\n")
    return base64.b64encode(key.public_key().public_bytes_raw()).decode()


def private_key(key_path: str):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    with open(os.path.expanduser(key_path), encoding="utf-8") as handle:
        raw = base64.b64decode(handle.read().strip(), validate=True)
    return Ed25519PrivateKey.from_private_bytes(raw)


def sign(digest: str, key_path: str) -> str:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        sys.exit("the digest must look like sha256:<64 hex digits>")
    message = b"AIAB/plan-bundle/v1\0" + f'{{"plan_digest":"{digest}"}}'.encode()
    return base64.b64encode(private_key(key_path).sign(message)).decode()


def pubkey(key_path: str) -> str:
    return base64.b64encode(private_key(key_path).public_key().public_bytes_raw()).decode()


def certify(root_path: str, role: str, signing_key: str) -> str:
    """The certificate as one JSON line, in the canonical (RFC 8785) form the box reads:
    keys in order, no spaces; nothing in the values needs escaping."""
    if not re.fullmatch(r"[A-Za-z0-9+/]{43}=", signing_key):
        sys.exit("the signing key must be the 44-character base64 line ending in =")
    root = private_key(root_path)
    root_public = base64.b64encode(root.public_key().public_bytes_raw()).decode()
    if signing_key == root_public:
        sys.exit(
            "that is the root's own key: certify a separate signing key, the root never signs plans"
        )
    statement = f'{{"role":"{role}","signing_key":"{signing_key}"}}'
    signature = base64.b64encode(root.sign(b"AIAB/signing-key/v1\0" + statement.encode()))
    return f'{{"role":"{role}","signature":"{signature.decode()}","signing_key":"{signing_key}"}}'


def read_certificate(path: str) -> str:
    with open(os.path.expanduser(path), encoding="utf-8") as handle:
        return handle.read().strip()


def submit(url: str, user: str, party: str, signature: str, certificate: str) -> str:
    """Log in to the UI and post the signature, the same as pasting it on the Sign screen."""
    import http.cookiejar
    import urllib.error
    import urllib.parse
    import urllib.request

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    base = url.split("/a/", 1)[0]
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPCookieProcessor(jar))

    def post(target: str, fields: dict[str, str]) -> tuple[int, str]:
        data = urllib.parse.urlencode(fields).encode()
        request = urllib.request.Request(target, data=data, method="POST")
        try:
            with opener.open(request, timeout=30) as response:
                return response.status, response.headers.get("Location", "")
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get("Location", "")

    password = getpass.getpass(f"UI password for {user}: ")
    status, _ = post(base + "/login", {"username": user, "password": password, "next": "/"})
    if status != 303:
        sys.exit("login failed: unknown account or wrong password")
    status, location = post(
        url, {"party": party, "signature": signature, "certificate": certificate}
    )
    for key in ("msg=", "err="):
        if key in location:
            return urllib.parse.unquote(location.split(key, 1)[1])
    return f"unexpected answer: HTTP {status}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    k = commands.add_parser("keygen", help="make a key pair; print the public key")
    k.add_argument("--out", required=True, help="where to write the private key (new file)")
    pk = commands.add_parser("pubkey", help="print the public key of a key file")
    pk.add_argument("--key", required=True)
    c = commands.add_parser("certify", help="certify a signing key with your root; print it")
    c.add_argument("--root", required=True, help="your root private key file")
    c.add_argument("--role", choices=["lab", "auditor"], required=True)
    c.add_argument("--signing-key", required=True, help="the signing key's public key")
    s = commands.add_parser("sign", help="sign a digest, optionally submit it to the UI")
    s.add_argument("--digest", required=True)
    s.add_argument("--key", required=True)
    s.add_argument("--certificate", help="the certificate file for --key; required with --submit")
    s.add_argument("--submit", help="the agreement's sign/upload URL shown on the Sign screen")
    s.add_argument("--party", choices=["lab", "auditor"], help="required with --submit")
    s.add_argument("--user", help="your UI username, required with --submit")
    d = commands.add_parser("digest", help="digest of a downloaded plan.zip")
    d.add_argument("bundle")
    args = parser.parse_args()
    if args.command == "keygen":
        print(keygen(args.out))
    elif args.command == "pubkey":
        print(pubkey(args.key))
    elif args.command == "certify":
        print(certify(args.root, args.role, args.signing_key))
    elif args.command == "sign":
        signature = sign(args.digest, args.key)
        if args.submit:
            if not (args.party and args.user and args.certificate):
                sys.exit("--submit needs --party, --user and --certificate")
            certificate = read_certificate(args.certificate)
            print(submit(args.submit, args.user, args.party, signature, certificate))
        else:
            print(signature)
    else:
        with zipfile.ZipFile(args.bundle) as archive:
            files = {n: archive.read(n) for n in archive.namelist() if not n.endswith("/")}
        print(folder_digest(files))


if __name__ == "__main__":
    main()
