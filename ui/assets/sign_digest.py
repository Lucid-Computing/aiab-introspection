#!/usr/bin/env python3
"""Your side of signing an AIAB agreement, in one small tool. Your private key never leaves
this machine.

Needs Python 3.10+ and the "cryptography" package (pip install cryptography).

  python3 sign_digest.py keygen --out ~/lab.key
      makes your key pair; prints the public key to submit on the Sign screen

  python3 sign_digest.py sign --digest sha256:... --key ~/lab.key \\
      --party lab --user teddy --submit http://127.0.0.1:8090/a/<agreement>/sign/upload
      signs the digest and submits the signature to the UI (asks for your UI password);
      without --submit it just prints the signature to paste

  python3 sign_digest.py digest plan.zip
      prints the digest of a downloaded plan bundle, to compare with the website

The key file holds one line: the base64 of a raw 32-byte Ed25519 private key.
The signed bytes are exactly what the box verifies:
  "AIAB/plan-bundle/v1\\0" + '{"plan_digest":"sha256:..."}'
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


def sign(digest: str, key_path: str) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        sys.exit("the digest must look like sha256:<64 hex digits>")
    with open(os.path.expanduser(key_path), encoding="utf-8") as handle:
        raw = base64.b64decode(handle.read().strip(), validate=True)
    message = b"AIAB/plan-bundle/v1\0" + f'{{"plan_digest":"{digest}"}}'.encode()
    signature = Ed25519PrivateKey.from_private_bytes(raw).sign(message)
    return base64.b64encode(signature).decode()


def submit(url: str, user: str, party: str, signature: str) -> str:
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
    status, location = post(url, {"party": party, "signature": signature})
    for key in ("msg=", "err="):
        if key in location:
            return urllib.parse.unquote(location.split(key, 1)[1])
    return f"unexpected answer: HTTP {status}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    k = commands.add_parser("keygen", help="make a key pair; print the public key")
    k.add_argument("--out", required=True, help="where to write the private key (new file)")
    s = commands.add_parser("sign", help="sign a digest, optionally submit it to the UI")
    s.add_argument("--digest", required=True)
    s.add_argument("--key", required=True)
    s.add_argument("--submit", help="the agreement's sign/upload URL shown on the Sign screen")
    s.add_argument("--party", choices=["lab", "auditor"], help="required with --submit")
    s.add_argument("--user", help="your UI username, required with --submit")
    d = commands.add_parser("digest", help="digest of a downloaded plan.zip")
    d.add_argument("bundle")
    args = parser.parse_args()
    if args.command == "keygen":
        print(keygen(args.out))
    elif args.command == "sign":
        signature = sign(args.digest, args.key)
        if args.submit:
            if not (args.party and args.user):
                sys.exit("--submit needs --party and --user")
            print(submit(args.submit, args.user, args.party, signature))
        else:
            print(signature)
    else:
        with zipfile.ZipFile(args.bundle) as archive:
            files = {n: archive.read(n) for n in archive.namelist() if not n.endswith("/")}
        print(folder_digest(files))


if __name__ == "__main__":
    main()
