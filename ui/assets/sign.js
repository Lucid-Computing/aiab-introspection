// Signing in the browser. The party's Ed25519 signing key is made here, kept in this
// browser's IndexedDB as a non-extractable WebCrypto key, and signs the plan digest here.
// The box trusts one root key per party; the party's root certifies this signing key
// (sign_digest.py certify, on the party's own machine), and the certificate is registered
// here and sent with every signature. The server only ever receives public keys,
// certificates and signatures, through the same forms the manual openssl route uses.
//
// The signed messages are exactly what static/aiab/audit/protocol.py signs and checks:
// "AIAB/plan-bundle/v1" + NUL + the canonical JSON {"plan_digest":"sha256:..."}, and for
// a certificate "AIAB/signing-key/v1" + NUL + {"role":"lab","signing_key":"..."}.
//
// The pure parts are exposed as globalThis.AiabSigning so that a Node script (the test in
// ui/tests/test_browser_signing.py) can check the bytes against the Python verifier.
// The page wiring runs only when a document exists.
(function (root) {
  "use strict";

  var PREFIX = "AIAB/plan-bundle/v1\u0000";
  var CERTIFICATE_PREFIX = "AIAB/signing-key/v1\u0000";
  var BACKUP_FORMAT = "aiab-signing-key/v1";
  var PBKDF2_ITERATIONS = 600000;
  var DB_NAME = "aiab-signing";
  var STORE = "keys";
  var subtle = root.crypto && root.crypto.subtle;

  function b64(bytes) {
    var view = new Uint8Array(bytes), text = "";
    for (var i = 0; i < view.length; i++) text += String.fromCharCode(view[i]);
    return btoa(text);
  }

  function unb64(text) {
    var raw = atob(text), out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  function checkDigest(digest) {
    if (!/^sha256:[0-9a-f]{64}$/.test(digest)) throw new Error("not a plan digest: " + digest);
    return digest;
  }

  // The bytes the signature covers. The JSON is written out by hand so that it is the
  // RFC 8785 form byte for byte: one key, no spaces, the digest has no escapable character.
  function messageBytes(digest) {
    return new TextEncoder().encode(PREFIX + '{"plan_digest":"' + checkDigest(digest) + '"}');
  }

  // The certificate as the box reads it: role, signature, signing_key, the canonical
  // (sorted, unspaced) form. Anything else in the pasted text is refused.
  function parseCertificate(text) {
    var raw;
    try { raw = JSON.parse(text); } catch (e) { throw new Error("not JSON"); }
    if (!raw || typeof raw !== "object" || Object.keys(raw).sort().join() !== "role,signature,signing_key") {
      throw new Error("expected a certificate with role, signature and signing_key");
    }
    if (raw.role !== "lab" && raw.role !== "auditor") throw new Error("bad role");
    if (!/^[A-Za-z0-9+/]{43}=$/.test(raw.signing_key)) throw new Error("bad signing key");
    if (typeof raw.signature !== "string" || raw.signature.length > 256) throw new Error("bad signature");
    // Whitespace around the signature is accepted by the verifier (it strips it); drop it
    // here so the stored and sent certificate is the plain base64 line.
    return { role: raw.role, signature: raw.signature.trim(), signing_key: raw.signing_key };
  }

  // The canonical (RFC 8785) text: keys in order, strings escaped as JSON does. The
  // signature is a base64 line but the schema allows other characters, so no shortcut.
  function certificateText(certificate) {
    return '{"role":' + JSON.stringify(certificate.role) + ',"signature":' + JSON.stringify(certificate.signature) +
      ',"signing_key":' + JSON.stringify(certificate.signing_key) + '}';
  }

  function certificateMessageBytes(certificate) {
    return new TextEncoder().encode(
      CERTIFICATE_PREFIX + '{"role":"' + certificate.role + '","signing_key":"' + certificate.signing_key + '"}'
    );
  }

  // Did this root (the party's public key in the trust file) sign the certificate?
  function verifyCertificate(certificate, rootKey) {
    if (!/^[A-Za-z0-9+/]{43}=$/.test(rootKey)) return Promise.resolve(false);
    return subtle.importKey("raw", unb64(rootKey), { name: "Ed25519" }, false, ["verify"])
      .then(function (root) {
        return subtle.verify({ name: "Ed25519" }, root, unb64(certificate.signature), certificateMessageBytes(certificate));
      }, function () { return false; });
  }

  // Ed25519 is available when generateKey accepts it; older browsers throw.
  function supported() {
    if (!subtle) return Promise.resolve(false);
    return subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"])
      .then(function () { return true; }, function () { return false; });
  }

  // A fresh key pair: the private key in PKCS#8 (for the backup) and the public key as the
  // 32 raw bytes in base64, the form the trust file holds.
  function generate() {
    return subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]).then(function (pair) {
      return Promise.all([
        subtle.exportKey("pkcs8", pair.privateKey),
        publicKeyBase64(pair.publicKey),
      ]).then(function (parts) { return { pkcs8: parts[0], publicKey: parts[1] }; });
    });
  }

  function publicKeyBase64(publicKey) {
    return subtle.exportKey("raw", publicKey).then(function (raw) {
      if (raw.byteLength !== 32) throw new Error("unexpected public key length");
      return b64(raw);
    }, function () {
      // No raw export: the last 32 bytes of the SubjectPublicKeyInfo are the key.
      return subtle.exportKey("spki", publicKey).then(function (spki) { return b64(spki.slice(-32)); });
    });
  }

  // The private key as this browser keeps it: not extractable, so no script, extension or
  // later page can read it out; it can only sign.
  function importPrivate(pkcs8) {
    return subtle.importKey("pkcs8", pkcs8, { name: "Ed25519" }, false, ["sign"]);
  }

  // The public key that belongs to a PKCS#8 private key, read back by importing it as
  // extractable once (the extractable copy is dropped) and deriving the public half.
  function publicKeyOf(pkcs8) {
    return subtle.importKey("pkcs8", pkcs8, { name: "Ed25519" }, true, ["sign"]).then(function (key) {
      return subtle.exportKey("jwk", key);
    }).then(function (jwk) {
      delete jwk.d;
      jwk.key_ops = ["verify"];
      return subtle.importKey("jwk", jwk, { name: "Ed25519" }, true, ["verify"]);
    }).then(publicKeyBase64);
  }

  function sign(privateKey, digest) {
    return subtle.sign({ name: "Ed25519" }, privateKey, messageBytes(digest)).then(b64);
  }

  function deriveKek(password, salt, iterations, usage) {
    return subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveKey"])
      .then(function (base) {
        return subtle.deriveKey(
          { name: "PBKDF2", hash: "SHA-256", salt: salt, iterations: iterations },
          base, { name: "AES-GCM", length: 256 }, false, [usage]
        );
      });
  }

  // The backup file: the PKCS#8 key under AES-GCM with a key derived from the password.
  // The public key and party travel in clear so a file can be told apart before decrypting.
  function encryptBackup(pkcs8, password, publicKey, party) {
    var salt = root.crypto.getRandomValues(new Uint8Array(16));
    var iv = root.crypto.getRandomValues(new Uint8Array(12));
    return deriveKek(password, salt, PBKDF2_ITERATIONS, "encrypt").then(function (kek) {
      return subtle.encrypt({ name: "AES-GCM", iv: iv }, kek, pkcs8);
    }).then(function (ciphertext) {
      return {
        format: BACKUP_FORMAT,
        party: party,
        public_key: publicKey,
        kdf: "pbkdf2-sha256",
        iterations: PBKDF2_ITERATIONS,
        salt: b64(salt),
        iv: b64(iv),
        ciphertext: b64(ciphertext),
      };
    });
  }

  function decryptBackup(backup, password) {
    if (!backup || backup.format !== BACKUP_FORMAT || backup.kdf !== "pbkdf2-sha256") {
      return Promise.reject(new Error("not an AIAB signing key backup"));
    }
    var iterations = backup.iterations | 0;
    if (iterations < 1 || iterations > 10000000) return Promise.reject(new Error("bad backup"));
    return deriveKek(password, unb64(backup.salt), iterations, "decrypt").then(function (kek) {
      return subtle.decrypt({ name: "AES-GCM", iv: unb64(backup.iv) }, kek, unb64(backup.ciphertext));
    }).then(null, function () { throw new Error("wrong password, or the file is damaged"); });
  }

  // IndexedDB keeps CryptoKey objects as they are, non-extractable included.
  function openDb() {
    return new Promise(function (resolve, reject) {
      var request = root.indexedDB.open(DB_NAME, 1);
      request.onupgradeneeded = function () { request.result.createObjectStore(STORE); };
      request.onsuccess = function () { resolve(request.result); };
      request.onerror = function () { reject(request.error); };
    });
  }

  function withStore(mode, work) {
    return openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(STORE, mode), request = work(tx.objectStore(STORE));
        tx.oncomplete = function () { resolve(request && request.result); db.close(); };
        tx.onerror = function () { reject(tx.error); db.close(); };
        tx.onabort = function () { reject(tx.error); db.close(); };
      });
    });
  }

  function loadKey(party) { return withStore("readonly", function (s) { return s.get(party); }); }
  function storeKey(party, entry) { return withStore("readwrite", function (s) { return s.put(entry, party); }); }
  function forgetKey(party) { return withStore("readwrite", function (s) { return s.delete(party); }); }

  root.AiabSigning = {
    PREFIX: PREFIX,
    CERTIFICATE_PREFIX: CERTIFICATE_PREFIX,
    messageBytes: messageBytes,
    parseCertificate: parseCertificate,
    certificateText: certificateText,
    certificateMessageBytes: certificateMessageBytes,
    verifyCertificate: verifyCertificate,
    supported: supported,
    generate: generate,
    importPrivate: importPrivate,
    publicKeyOf: publicKeyOf,
    sign: sign,
    encryptBackup: encryptBackup,
    decryptBackup: decryptBackup,
    b64: b64,
    unb64: unb64,
  };

  if (typeof document === "undefined") return;

  // ---- the Sign screen ----

  function confirmDialog(text) {
    var dialog = document.getElementById("confirm");
    if (!dialog || !dialog.showModal) return Promise.resolve(root.confirm(text));
    return new Promise(function (resolve) {
      document.getElementById("confirm-text").textContent = text;
      dialog.returnValue = "";
      dialog.addEventListener("close", function () { resolve(dialog.returnValue === "ok"); }, { once: true });
      dialog.showModal();
    });
  }

  function download(name, text) {
    var blob = new Blob([text], { type: "application/json" });
    var url = URL.createObjectURL(blob), a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  function readFile(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(reader.result); };
      reader.onerror = function () { reject(reader.error); };
      reader.readAsText(file);
    });
  }

  function wire(box) {
    var party = box.dataset.party, digest = box.dataset.digest, trusted = box.dataset.trusted || "";
    var states = box.querySelectorAll("[data-state]");
    var note = box.querySelector(".browser-key-note");
    var uploadForm = document.getElementById("upload-form-" + party);
    var sigInput = document.getElementById("sig-" + party);
    var certInput = document.getElementById("cert-" + party);

    function show(state) {
      states.forEach(function (el) { el.hidden = el.dataset.state !== state; });
    }

    function tell(text, bad) {
      if (!note) return;
      note.textContent = text || "";
      note.hidden = !text;
      note.classList.toggle("field-hint--error", !!bad);
    }

    // Is the stored certificate one the party's root (on production) made for this key?
    function certificateVerdict(entry) {
      var certificate = entry.certificate;
      if (!certificate) return Promise.resolve({ ok: false, text: "No certificate registered yet." });
      if (certificate.role !== party || certificate.signing_key !== entry.publicKey) {
        return Promise.resolve({ ok: false, text: "The registered certificate is for another key or role." });
      }
      return verifyCertificate(certificate, trusted).then(function (ok) {
        return ok
          ? { ok: true, text: "Certified by the " + party + " root production trusts." }
          : { ok: false, text: "The registered certificate was not made by the " + party + " root production trusts." };
      });
    }

    function render(entry) {
      if (!entry) { show("none"); return; }
      box.querySelectorAll(".browser-public-key").forEach(function (el) { el.textContent = entry.publicKey; });
      var verdict = box.querySelector(".browser-key-verdict"), certify = box.querySelector(".browser-certify-block");
      var signButton = box.querySelector(".browser-sign");
      return certificateVerdict(entry).then(function (result) {
        verdict.textContent = result.text;
        verdict.className = "browser-key-verdict selected-label " + (result.ok ? "ok" : "bad");
        certify.hidden = result.ok;
        signButton.disabled = !result.ok;
        show("ready");
      });
    }

    function refresh() {
      return loadKey(party).then(render, function (error) { tell("This browser's key store is not available: " + error, true); show("none"); });
    }

    function makeKey(event) {
      event.preventDefault();
      var form = event.target, password = form.elements.password.value, again = form.elements.again.value;
      if (!password) { tell("Choose a backup password first.", true); return; }
      if (password !== again) { tell("The two passwords differ.", true); return; }
      tell("Making the key and its backup…");
      var made;
      generate().then(function (pair) {
        made = pair;
        return encryptBackup(pair.pkcs8, password, pair.publicKey, party);
      }).then(function (backup) {
        download("aiab-" + party + "-signing-key.json", JSON.stringify(backup, null, 2) + "\n");
        return importPrivate(made.pkcs8);
      }).then(function (privateKey) {
        return storeKey(party, { privateKey: privateKey, publicKey: made.publicKey, made: new Date().toISOString() });
      }).then(function () {
        form.reset();
        tell("Key made. The backup file is downloading: keep it and its password, there is no other copy.");
        return refresh();
      }).catch(function (error) { tell("The key was not made: " + error.message, true); });
    }

    function importKey(event) {
      event.preventDefault();
      var form = event.target, file = form.elements.backup.files[0], password = form.elements.password.value;
      if (!file) { tell("Choose the backup file first.", true); return; }
      tell("Reading the backup…");
      var backup;
      readFile(file).then(function (text) {
        backup = JSON.parse(text);
        if (backup.party && backup.party !== party) throw new Error("that backup is the " + backup.party + " key");
        return decryptBackup(backup, password);
      }).then(function (pkcs8) {
        return Promise.all([importPrivate(pkcs8), publicKeyOf(pkcs8)]);
      }).then(function (parts) {
        if (backup.public_key && backup.public_key !== parts[1]) throw new Error("the backup's public key does not match its private key");
        return storeKey(party, { privateKey: parts[0], publicKey: parts[1], made: new Date().toISOString() });
      }).then(function () {
        form.reset();
        tell("Key imported into this browser.");
        return refresh();
      }).catch(function (error) { tell("The key was not imported: " + error.message, true); });
    }

    // The certificate the party's root made for this browser's key, pasted here once.
    function registerCertificate(event) {
      event.preventDefault();
      var form = event.target, text = form.elements.certificate.value;
      var certificate;
      loadKey(party).then(function (entry) {
        if (!entry) throw new Error("there is no key in this browser");
        certificate = parseCertificate(text);
        if (certificate.role !== party) throw new Error("that certificate is for the " + certificate.role);
        if (certificate.signing_key !== entry.publicKey) throw new Error("that certificate names another key, not this browser's");
        return verifyCertificate(certificate, trusted).then(function (ok) {
          if (!ok) throw new Error("the " + party + " root production trusts did not sign it");
          entry.certificate = certificate;
          return storeKey(party, entry);
        });
      }).then(function () {
        form.reset();
        tell("Certificate registered. This browser's key can sign now.");
        return refresh();
      }).catch(function (error) { tell("The certificate was not registered: " + error.message, true); });
    }

    function signNow() {
      loadKey(party).then(function (entry) {
        if (!entry) { tell("There is no key in this browser.", true); return; }
        return certificateVerdict(entry).then(function (result) {
          if (!result.ok) { tell(result.text, true); return; }
          return confirmDialog(
            "Sign this version, digest " + digest + ", with the " + party + " key in this browser (" +
            entry.publicKey + ")? Check the digest against the plan zip first if you want to."
          ).then(function (ok) {
            if (!ok) return;
            tell("Signing…");
            return sign(entry.privateKey, digest).then(function (signature) {
              if (!uploadForm || !sigInput || !certInput) throw new Error("the upload form is missing");
              sigInput.value = signature;
              certInput.value = certificateText(entry.certificate);
              uploadForm.requestSubmit();
            });
          });
        });
      }).catch(function (error) { tell("Not signed: " + error.message, true); });
    }

    function forget() {
      confirmDialog(
        "Forget the " + party + " key in this browser? Only the backup file and its password can bring it back."
      ).then(function (ok) {
        if (!ok) return;
        return forgetKey(party).then(function () { tell("Key forgotten."); return refresh(); });
      });
    }

    box.querySelector(".browser-make").addEventListener("submit", makeKey);
    box.querySelector(".browser-import").addEventListener("submit", importKey);
    box.querySelector(".browser-certify").addEventListener("submit", registerCertificate);
    box.querySelector(".browser-sign").addEventListener("click", signNow);
    box.querySelector(".browser-forget").addEventListener("click", forget);

    supported().then(function (ok) {
      if (!ok || !root.indexedDB) { show("unsupported"); return; }
      return refresh();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".browser-key[data-party]").forEach(wire);
  });
})(globalThis);
