// Signing in the browser, with nothing to run on the party's own machine.
//
// The box trusts one root key per party and accepts a plan signature only from a signing
// key that root has approved (a certificate). "Set up signing" makes both here: the root,
// which leaves the page only as the password-protected key file that downloads, and the
// signing key, kept in this browser's IndexedDB as a non-extractable WebCrypto key with
// the certificate the root made for it. The root is never stored in the browser and never
// sent to the server. Another browser uses the key file: the page opens it, approves a
// fresh signing key with the root, and forgets the root again. The server only ever
// receives public keys, certificates and signatures.
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

  // The certificate a root (a CryptoKey that can sign) gives a signing public key.
  function certify(rootPrivateKey, role, signingKey) {
    var certificate = { role: role, signing_key: signingKey, signature: "" };
    return subtle.sign({ name: "Ed25519" }, rootPrivateKey, certificateMessageBytes(certificate))
      .then(function (signature) { certificate.signature = b64(signature); return certificate; });
  }

  // A fresh signing key approved by the given root: what this browser keeps.
  function approvedSigningKey(rootPrivateKey, rootPublicKey, role) {
    return generate().then(function (pair) {
      return Promise.all([importPrivate(pair.pkcs8), certify(rootPrivateKey, role, pair.publicKey)])
        .then(function (parts) {
          return {
            privateKey: parts[0],
            publicKey: pair.publicKey,
            certificate: parts[1],
            rootPublicKey: rootPublicKey,
            made: new Date().toISOString(),
          };
        });
    });
  }

  // Set up signing: a root and an approved signing key. Returns the key file (the root,
  // encrypted with the password) and the entry to store. The root is dropped afterwards.
  function setUp(party, password) {
    return generate().then(function (root) {
      return Promise.all([
        encryptBackup(root.pkcs8, password, root.publicKey, party),
        importPrivate(root.pkcs8).then(function (rootKey) {
          return approvedSigningKey(rootKey, root.publicKey, party);
        }),
      ]).then(function (parts) { return { keyFile: parts[0], entry: parts[1] }; });
    });
  }

  // Another browser: open the key file, approve a fresh signing key, drop the root.
  function fromKeyFile(keyFile, password, party) {
    return decryptBackup(keyFile, password).then(function (pkcs8) {
      return Promise.all([importPrivate(pkcs8), publicKeyOf(pkcs8)]);
    }).then(function (parts) {
      if (keyFile.public_key && keyFile.public_key !== parts[1]) {
        throw new Error("the file's public key does not match its private key");
      }
      return approvedSigningKey(parts[0], parts[1], party);
    });
  }

  // A browser set up before root keys holds one key, which production may trust as the
  // root: approve a fresh signing key with it. The old key is then no longer kept here;
  // its key file is the root's key file.
  function fromStoredKey(entry, party) {
    return approvedSigningKey(entry.privateKey, entry.publicKey, party);
  }

  // What a browser's key can do now for a party whose trusted root is `trusted`.
  // "ready": sign. "waiting": the box does not trust this browser's root yet. "old": a key
  // from before root keys that production trusts as the root. "stale": anything else;
  // set up again.
  function standing(entry, party, trusted) {
    if (!entry.certificate) {
      return Promise.resolve(entry.publicKey === trusted ? "old" : "stale");
    }
    if (entry.certificate.role !== party || entry.certificate.signing_key !== entry.publicKey) {
      return Promise.resolve("stale");
    }
    // Keys registered before setup recorded their root carry no rootPublicKey: judge
    // them by whether the trusted root made their certificate.
    if (entry.rootPublicKey && entry.rootPublicKey !== trusted) return Promise.resolve("waiting");
    return verifyCertificate(entry.certificate, trusted).then(function (ok) { return ok ? "ready" : "stale"; });
  }

  // A signing key for a server-certified browser: made non-extractable from the start, so
  // the private half never exists in a readable form; only the public half leaves.
  function freshSigningKey() {
    return subtle.generateKey({ name: "Ed25519" }, false, ["sign", "verify"]).then(function (pair) {
      return publicKeyBase64(pair.publicKey).then(function (publicKey) {
        return { privateKey: pair.privateKey, publicKey: publicKey };
      });
    });
  }

  // Get a fresh browser key certified by the party root the server holds. The certificate
  // is checked here against the trusted root before the key is kept. `post` sends JSON and
  // resolves to the parsed reply (fetch in the page; a stub in tests).
  function enroll(party, trusted, post) {
    return freshSigningKey().then(function (pair) {
      return post({ party: party, signing_key: pair.publicKey }).then(function (reply) {
        var certificate = reply && reply.certificate;
        if (!certificate) throw new Error((reply && reply.error) || "the server did not certify the key");
        if (certificate.role !== party || certificate.signing_key !== pair.publicKey) {
          throw new Error("the server certified a different key");
        }
        return verifyCertificate(certificate, trusted).then(function (ok) {
          if (!ok) throw new Error("the certificate is not from the " + party + " root the box trusts");
          return {
            privateKey: pair.privateKey,
            publicKey: pair.publicKey,
            certificate: certificate,
            rootPublicKey: trusted,
            made: new Date().toISOString(),
            issued: true,
          };
        });
      });
    });
  }

  root.AiabSigning = {
    PREFIX: PREFIX,
    CERTIFICATE_PREFIX: CERTIFICATE_PREFIX,
    messageBytes: messageBytes,
    parseCertificate: parseCertificate,
    certificateText: certificateText,
    certificateMessageBytes: certificateMessageBytes,
    verifyCertificate: verifyCertificate,
    certify: certify,
    setUp: setUp,
    fromKeyFile: fromKeyFile,
    fromStoredKey: fromStoredKey,
    standing: standing,
    freshSigningKey: freshSigningKey,
    enroll: enroll,
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

  function signQuestion(digest, party) {
    return "Sign this version, digest " + digest + ", as the " + party + "? Check the digest against the plan zip first if you want to.";
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
    var keyForm = document.getElementById("key-form-" + party);
    var keyInput = document.getElementById("key-" + party);
    var uploadForm = document.getElementById("upload-form-" + party);
    var sigInput = document.getElementById("sig-" + party);
    var certInput = document.getElementById("cert-" + party);
    var verdict = box.querySelector(".browser-key-verdict");
    var signButton = box.querySelector(".browser-sign");
    var submitButton = box.querySelector(".browser-submit-root");
    var continueButton = box.querySelector(".browser-continue");

    function show(state) {
      states.forEach(function (el) { el.hidden = el.dataset.state !== state; });
    }

    function tell(text, bad) {
      if (!note) return;
      note.textContent = text || "";
      note.hidden = !text;
      note.classList.toggle("field-hint--error", !!bad);
    }

    function short(key) { return key ? key.slice(0, 8) + "…" : "none"; }

    var VERDICT = {
      ready: ["Ready to sign.", "ok"],
      waiting: ["Waiting for the box to trust your key. Submit it if you have not; the release takes a few minutes.", "bad"],
      old: ["This browser has your key from before. Continue to keep using it.", "bad"],
    };

    function render(entry) {
      if (!entry) { show("none"); return; }
      return standing(entry, party, trusted).then(function (state) {
        if (state === "stale") {
          tell("This browser's " + party + " key is not one the box trusts. Set up signing again, or use your key file.", true);
          show("none");
          return;
        }
        box.querySelector(".browser-public-key").textContent = entry.publicKey;
        verdict.textContent = VERDICT[state][0];
        verdict.className = "browser-key-verdict selected-label " + VERDICT[state][1];
        signButton.hidden = state !== "ready";
        submitButton.hidden = state !== "waiting";
        continueButton.hidden = state !== "old";
        show("ready");
      });
    }

    function refresh() {
      return loadKey(party).then(render, function (error) { tell("This browser's key store is not available: " + error, true); show("none"); });
    }

    // Send the root to production through the existing root form: a pull request, then the
    // release that builds it into the box. Every page then waits until the box trusts it.
    function submitRoot(rootPublicKey) {
      if (!keyForm || !keyInput) { tell("The root form is missing on this page.", true); return Promise.resolve(); }
      var question = trusted
        ? "The box trusts another " + party + " key (" + short(trusted) + "). Replace it with yours (" + short(rootPublicKey) +
          ")? Signatures approved by the old key stop counting after the release."
        : "Submit your " + party + " key (" + short(rootPublicKey) + ")? The box trusts it after a release of a few minutes.";
      return confirmDialog(question).then(function (ok) {
        if (!ok) { tell("Not submitted. Submit it whenever you are ready."); return; }
        keyInput.value = rootPublicKey;
        keyForm.requestSubmit();
      });
    }

    function setUpHere(event) {
      event.preventDefault();
      var form = event.target, password = form.elements.password.value, again = form.elements.again.value;
      if (!password) { tell("Choose a password first.", true); return; }
      if (password !== again) { tell("The two passwords differ.", true); return; }
      tell("Setting up…");
      var made;
      setUp(party, password).then(function (result) {
        made = result;
        download("aiab-" + party + "-key.json", JSON.stringify(result.keyFile, null, 2) + "\n");
        return storeKey(party, result.entry);
      }).then(function () {
        form.reset();
        tell("Set up. Your key file is downloading: keep it and its password, they are the only way to sign as the " + party + " in another browser.");
        return refresh();
      }).then(function () {
        if (made.entry.rootPublicKey !== trusted) return submitRoot(made.entry.rootPublicKey);
      }).catch(function (error) { tell("Not set up: " + error.message, true); });
    }

    function useKeyFile(event) {
      event.preventDefault();
      var form = event.target, file = form.elements.backup.files[0], password = form.elements.password.value;
      if (!file) { tell("Choose your key file first.", true); return; }
      tell("Opening the key file…");
      readFile(file).then(function (text) {
        var keyFile = JSON.parse(text);
        if (keyFile.party && keyFile.party !== party) throw new Error("that is the " + keyFile.party + "'s key file");
        return fromKeyFile(keyFile, password, party);
      }).then(function (entry) {
        return storeKey(party, entry).then(function () { return entry; });
      }).then(function (entry) {
        form.reset();
        tell(entry.rootPublicKey === trusted ? "Ready to sign in this browser." : "Key file opened. The box does not trust this key yet.");
        return refresh();
      }).catch(function (error) { tell("The key file was not used: " + error.message, true); });
    }

    function continueWithStoredKey() {
      loadKey(party).then(function (entry) {
        if (!entry) return;
        return fromStoredKey(entry, party).then(function (next) {
          return storeKey(party, next);
        }).then(function () {
          tell("Done. The key file you saved for this browser before is now your " + party + " key file: keep it.");
          return refresh();
        });
      }).catch(function (error) { tell("Not continued: " + error.message, true); });
    }

    function submitStoredRoot() {
      loadKey(party).then(function (entry) {
        if (entry && entry.rootPublicKey) return submitRoot(entry.rootPublicKey);
      });
    }

    function signNow() {
      loadKey(party).then(function (entry) {
        if (!entry) { tell("There is no key in this browser.", true); return; }
        return standing(entry, party, trusted).then(function (state) {
          if (state !== "ready") { tell("This browser cannot sign yet.", true); return; }
          return confirmDialog(signQuestion(digest, party)).then(function (ok) {
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
        "Forget the " + party + " key in this browser? Your key file and its password set it up again."
      ).then(function (ok) {
        if (!ok) return;
        return forgetKey(party).then(function () { tell("Forgotten."); return refresh(); });
      });
    }

    box.querySelector(".browser-make").addEventListener("submit", setUpHere);
    var importForm = box.querySelector(".browser-import");
    if (importForm) importForm.addEventListener("submit", useKeyFile);
    submitButton.addEventListener("click", submitStoredRoot);
    continueButton.addEventListener("click", continueWithStoredKey);
    signButton.addEventListener("click", signNow);
    box.querySelector(".browser-forget").addEventListener("click", forget);

    supported().then(function (ok) {
      if (!ok || !root.indexedDB) { show("unsupported"); return; }
      return refresh();
    });
  }

  // ---- Sign, from the plan and the agreements pages ----
  //
  // A link to the Sign screen with data-party, data-digest, data-trusted, data-upload and
  // data-next. When this browser holds a key the box trusts, the click signs here and posts
  // the signature; the Sign screen is only for a browser that is not set up (or cannot be).
  // The link's href is the fall-back; ?why= tells that screen what was missing.

  function postForm(action, fields) {
    var form = document.createElement("form");
    form.method = "post";
    form.action = action;
    form.hidden = true;
    Object.keys(fields).forEach(function (name) {
      var input = document.createElement("input");
      input.type = "hidden";
      input.name = name;
      input.value = fields[name];
      form.appendChild(input);
    });
    document.body.appendChild(form);
    form.submit();
  }

  function wireQuickSign(link) {
    var party = link.dataset.party, digest = link.dataset.digest, trusted = link.dataset.trusted || "";
    var certifyUrl = link.dataset.certify || "";

    function postJson(body) {
      return root.fetch(certifyUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(function (response) { return response.json(); });
    }

    function fallBack(why, error) {
      var href = link.getAttribute("href");
      var separator = href.indexOf("?") === -1 ? "?" : "&";
      if (why) href += separator + "why=" + encodeURIComponent(why);
      else if (error) href += separator + "err=" + encodeURIComponent("Not signed: " + error);
      root.location.assign(href);
    }

    link.addEventListener("click", function (event) {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      if (link.dataset.busy) return;
      link.dataset.busy = "1";
      supported().then(function (ok) {
        if (!ok || !root.indexedDB) { fallBack("unsupported"); return; }
        return loadKey(party).then(function (entry) {
          if (!entry && !certifyUrl) { fallBack("none"); return; }
          return (entry ? standing(entry, party, trusted) : Promise.resolve("none")).then(function (state) {
            if (state !== "ready" && certifyUrl) {
              // First Sign in this browser, or a key from before: make one and have it certified.
              return enroll(party, trusted, postJson).then(function (fresh) {
                return storeKey(party, fresh).then(function () { return fresh; });
              });
            }
            if (state !== "ready") { fallBack(state); return null; }
            return entry;
          }).then(function (entry) {
            if (!entry) return;
            return confirmDialog(signQuestion(digest, party)).then(function (yes) {
              if (!yes) { delete link.dataset.busy; return; }
              return sign(entry.privateKey, digest).then(function (signature) {
                postForm(link.dataset.upload, {
                  party: party,
                  signature: signature,
                  certificate: certificateText(entry.certificate),
                  next: link.dataset.next || "",
                });
              });
            });
          });
        });
      }).catch(function (error) { fallBack(null, error.message || String(error)); });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".browser-key[data-party]").forEach(wire);
    document.querySelectorAll("a.quick-sign[data-party]").forEach(wireQuickSign);
  });
})(globalThis);
