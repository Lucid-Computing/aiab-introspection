# Version 1 threat model

This document states implemented controls, deployment requirements and limits.
Offline tests alone are not evidence that a particular deployment meets them. The
hosted service accepted on 2026-09-19 runs AIAB in a Tinfoil CPU enclave and uses a
separately verified Tinfoil classifier connection.

## Claim and observation boundary

AIAB provides attestable measurement of policy-relevant inference received and admitted
by this proxy, subject to TEE assumptions. Supported requests include the complete
submitted text message list, with roles. Unsupported tools, attachments, streaming,
and non-text content must be rejected rather than silently omitted. Counters describe
admitted requests, selected work, successful classifications, failures, forwarding,
flags, and blocks within finalized periods.

This does not establish datacenter-wide coverage, absence of prohibited activity,
or the fraction of compute spent on AI research. Direct inference access, other
models/endpoints, and internal agent activity may bypass observation. A network
uplink alone need not observe internal inference. Independent traffic-generator
counts demonstrate bypass in the demo; the auditor alone cannot detect it.

## Trusted components and assumptions

The trusted design includes the measured appliance image, frozen effective settings,
classifier definition and full system prompt, cryptographic libraries, enclave
runtime, and the verified classifier connection. Trust also depends on CPU/TEE
hardware, firmware, attestation roots and verification implementation, the correctness
of measurements and their binding to the secure channel, and the provider's serving
infrastructure. Tinfoil hosts the hardware and remains a party to these assumptions.
Attestation is not a proof of source correctness or freedom from hardware bugs,
side channels, or all information leaks.

The answer provider and webchat are outside the trusted system. The answer provider
receives forwarded conversation contents by design. The auditor
must approve image and policy digests independently; trusting values supplied by
the endpoint itself is circular. The policy manifest is derived from effective
startup settings and fetched over the same attested channel. Image identity alone
does not establish environment settings. Debug enclaves must be rejected.

The auditor pins the Tinfoil release's `tinfoil.hash`/Sigstore subject digest; the
measured release config separately pins the OCI application image digest. CLI
verification must succeed with complete code/enclave measurements and a matching
TLS key. Audit artifacts are fetched through the SDK's verified, pinned TLS
transport, with repo/release/code/key identity checked before and after each fetch.
An SDK re-verification after key rotation requires a fresh audit. An expected policy
manifest is generated offline from reviewed source/settings, never approved merely
by fetching the running server's manifest. The live `v0.1.1` deployment passed
hardware/release verification and independently pinned policy verification on
2026-09-19.

Classifier calls must exclusively use the reviewed Tinfoil SDK verification path.
The requested model identifier is not an immutable weight digest. Serving-code
measurements and the router release are exposed by the provider SDK. The model alias
does not prove an independently pinned immutable weight digest; downstream provider
verification remains part of the trust chain. Local self-signed
TLS and the mock classifier provide development behavior, not enclave guarantees.

The browser host, Caddy, and verified gateway remain outside the AIAB enclave and
can observe conversations. The gateway holds only the AIAB gateway credential;
provider credentials are injected into the enclave. The older self-hosted overlay
remains a rollback target and does not carry the live enclave claim. Secret files
are restricted to root/process ownership and mode 0400/0600 and excluded from
browser assets, images, logs, and audit artifacts.
An invalid credential file or ambiguous key sources stop startup. Failed SDK
verification keeps readiness and admission closed without a mock fallback.
Every admitted conversation reaches Tinfoil classification; only policy-allowed
messages reach Anthropic when both hosted overlays are enabled. Classifier
verification does not make Anthropic answer inference confidential or attested.

Party keys for the audit plan (lab and auditor, Ed25519) live with the parties, not in
the service. Each party has a root key, whose public half is baked into the box image
(`trust.json`) and covered by the attestation, and signing keys the root certifies: a
certificate is the root's signature over `{"role", "signing_key"}`, and a run carries each
party's certificate next to its plan signature. The box checks the certificate against
the baked root, then the plan signature against the certified key, and refuses a
certificate that names a root as the signing key, so a root cannot sign a plan through
the normal path. The box cannot see where a root lives or how a certified key was made. A new signing key needs no release; only a root change
goes through the trust file and a rebuild. The box keeps no state, so a certificate
cannot be revoked: a lost signing key stays valid until its party's root is rotated,
which is the same exposure a lost key had when keys were baked in. The box reads no
clock, so certificates carry no expiry. In the agreement UI, Set up signing makes both
keys in the party's browser: the root, which leaves the page only as a key file encrypted
under the party's password (PBKDF2, AES-GCM), and a signing key the root certifies at
once, kept in the browser as a non-extractable WebCrypto key with its certificate. The
root is not stored in the browser; it is in page memory during setup, and again when the
key file is opened to set up another browser. A page that serves malicious JavaScript at
those moments can take the root, so the UI server is trusted to serve honest JavaScript
for setup as it already is for signing. A party that wants its root never to touch the
browser keeps it elsewhere and uses the commands under Other ways to sign. The UI server
receives public keys, certificates and signatures only, except for the prototype
shortcut below. The UI server is otherwise trusted to serve honest JavaScript and an
honest digest, which is an active attack a party can detect by comparing the served page
with the repository and the shown digest with the downloaded plan files, and the trust
file change that admits a new public key is visible in GitHub history. Requiring the
other party's approval of a key change in `approve-keys.yml` is still open.

## Prototype shortcut: the server signs for the lab and certifies the auditors

To make signing one click, the UI server can hold two secrets:

- `AIAB_LAB_KEY` and `AIAB_LAB_CERTIFICATE`: a certified lab signing key. The server
  signs every plan version as the lab by itself.
- `AIAB_AUDITOR_ROOT_KEY`: the auditor root's private key. On an auditor's first Sign,
  the browser makes its own Ed25519 signing key, non-extractable from the start, and
  sends only the public half to `POST /sign/certificate`. The server certifies it for
  the logged-in auditor and appends a record (who, when, which key) to
  `issued-certificates.jsonl` next to the accounts file. The browser checks the
  certificate against the trusted root before keeping the key, then signs. Each auditor,
  and each browser, has a key of its own; nobody sets anything up.

The box's check does not change: it still requires both signatures, each from a key the
party's baked-in root certified. What changes is what a signature shows. With the lab
key and the auditor root on the server, whoever controls the server, or breaks into it,
can sign as the lab and can certify a new auditor key and sign with it. The two
signatures then show only that the server approved the version. A signature under an
existing auditor's key does show that this auditor's browser made it, because that
private key never leaves the browser; but the server can always make another auditor
key. The UI decides who is the auditor from the login (anyone not in `admins.json`), and
the lab runs the server, so in this mode the lab can sign as the auditor. A root on the
server also has more authority than a signing key would: the keys it certifies never
expire. The lab root's private half is not on the server.

What the real version changes, in order:

1. Each party makes its own root, on its own machine or with Set up signing in its own
   browser, and submits the public half. One PR and one release replace the roots, so
   no root's private half has ever been on the server or with the operator.
2. Unset `AIAB_LAB_KEY`, `AIAB_LAB_CERTIFICATE` and `AIAB_AUDITOR_ROOT_KEY`. The lab
   loses automatic signing and presses Sign like the auditor.
3. Each party runs its own certificate signer (the same protocol as
   `/sign/certificate`), on its own infrastructure, out of the lab's reach, authenticating
   its members with its own identity provider; or the root holder certifies members'
   keys by hand (`sign_digest.py certify`).
4. Each party confirms that the released box is one it approved; see the review below
   for why checking only the trust file is not enough.

Limits that remain in the real version: certificates cannot be revoked or expire (the
box keeps no state and reads no clock), so cutting off a key means rotating the root.

### Red-team review, 2026-11-10

An adversarial review of this design (each party keeps its own root and signer) found
that separate roots protect a party's approval only if the party also controls the page
it signs on and approves the box release itself. Findings, most severe first; CONFIRMED
means checked in the code, PLAUSIBLE means reasoned:

1. **The signing page is served by the lab (critical, CONFIRMED).** `sign.js` signs the
   digest the page gives it, with the key in IndexedDB, behind a confirmation the page
   itself draws. A malicious page can show plan A and sign plan B, or sign without a
   click; a non-extractable key cannot be read out but can still be used. A separate
   signer does not stop this while the auditor signs on the lab's site. Needed: the
   party signs on a page or client it controls, which shows the plan and computes the
   digest itself.
2. **Checking the trusted root is not enough (critical, PLAUSIBLE).** The lab controls the
   repository, `approve-keys.yml` (which checks the format and merges, without the other
   party's approval) and `release.yml` (which updates the pins itself). It can keep the
   auditor's root and release a box that does not check signatures; the attestation
   then truthfully measures that box. Needed: both parties approve and pin the box's
   measurement and build, and check the attestation outside the lab's UI
   (`static/auditor/audit.py` already verifies attestation; the UI's box status uses
   plain HTTPS, `ui/server/box.py`).
3. **A root on a server is a minting authority (critical).** Anyone with the server's
   root certifies keys that never expire. In this prototype the server can sign as the
   auditor anyway, so this adds little here; it must not carry into the real version.
4. **Key substitution at enrollment (high, PLAUSIBLE).** A malicious page can let the
   real auditor log in to the auditor's own signer and submit an attacker's public key
   instead of the browser's. Proving possession of a key does not help, since the
   attacker holds it. Needed: enrollment through an interface the auditor controls,
   binding the authenticated person, the role and the key their own device made.
5. **Login means auditor (high, CONFIRMED).** `ui/server/auth.py` treats every admitted
   login that is not in `admins.json` as the auditor. With automatic certificates, that
   is a permanent auditor key. Needed: explicit authorization to represent the party.
6. **No revocation; insiders keep their keys (high, CONFIRMED).** A departed or malicious
   member keeps a valid certificate until the root rotates, and an older box image still
   accepts it after. One insider cannot forge the other party's signature, but can abuse
   their own party's blanket authority.
7. **Approvals can be reused (medium, CONFIRMED).** A signature binds only the plan bytes,
   so an old signed bundle can be resubmitted later or shown under another agreement
   name; changing any byte, `plan_id` included, does break it. Per-boot deduplication
   (`static/aiab/baked/runtime.py`) is not replay protection. Whether an approval is for
   a plan or for one run is to be decided.

Checked and holding (CONFIRMED): the box refuses either party's root as a signing key and
one key for both parties, comparing bytes and rejecting non-canonical base64; a
certificate binds its role and verifies only against that role's root; certificate and
plan messages are domain-separated (`AIAB/signing-key/v1`, `AIAB/plan-bundle/v1`) and
canonicalized with RFC 8785. A lab key or lab root alone cannot make an auditor
certificate.

Must-have for the real version, from the review: party-controlled signing pages or
clients that render the plan and compute the digest; independent roots and signers, with
enrollment bound to identity, role and the device's key; both parties approving and
pinning the box measurement; defined reuse rules and compromise recovery; and the
signer's identity and scope in certificates if per-person attribution is promised (a box
schema change and a release). Nice to have: hardware-backed keys, a transparency log of
issued certificates, and more than one approver per party. Tracked in MAC-433.

## Data handling and observable channels

Request/response text must never be written to appliance disk, logs, reports, or
metrics. Signing keys, local TLS private material, processing queues, and retained
reports remain in memory. Operational logs use allowlisted counts, timings, and
error categories; no raw HTTP/SDK exception text or body debugging. Logging filters
and sentinel regression tests are defense in depth and cannot prove that arbitrary
plaintext or all covert channels are absent.

Expected outbound destinations are the configured governed inference origin, SDK
router discovery and selected router, GitHub/attestation certificate proxies and
Sigstore/TUF verification infrastructure. The template uses open public egress with
Tinfoil's private-range blocking because a complete dynamic hostname allowlist is
not established. Application URLs remain fixed/reviewed; this is not an externally
enforced destination allowlist or absence-of-exfiltration proof. See
[OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) for the inventory. No analytics or arbitrary
request-selected destinations are allowed. Browser traffic goes to the configured
AIAB endpoint and same-origin static assets. Observable outputs include signed reports,
public identity/manifest/health endpoints, HTTP responses and sizes, connection timing,
and allowlisted operational logs. The provider and network may observe traffic metadata.

The Tinfoil shim supplies the attested external TLS boundary and forwards HTTP to
the application's private port 8080. SDK public caches use tmpfs directories in the
template; no application volume persists. Ordinary browser and traffic-generator
connections use Web PKI HTTPS and do not independently attest the AIAB; only the
auditor and explicitly verified SDK/CLI client paths make that channel claim.

The fixed report schema is not a fixed byte length or a proven bandwidth limit.
Exact counts, particularly in short periods, can reveal information. Version 1 has
no differential privacy, minimum-cell protection, constant-traffic release mechanism,
or protection against all covert channels. Use synthetic conversations in the demo.

## Classification and enforcement limits

The classifier is a general chat model. Untrusted conversation text can attempt
prompt injection; delimiters and structured output do not provide a security boundary.
Confidence is an uncalibrated model score, not a probability. False positives, false
negatives, missing workflow context, and distributed evasion remain possible. A
blocked preset prompt illustrates endpoint enforcement, not adversarial robustness.

Monitor mode uses unverifiable Bernoulli request sampling. It forwards before
asynchronous classification; a later failure cannot undo traffic already forwarded.
Selected work that fails, times out, or cannot enter the bounded queue must become a
classification error. Request proportions are not token/compute proportions or
verified research prevalence. Small samples and classifier errors limit interpretation.

Block mode classifies every admitted request before forwarding, exposing 100% of
prompts to the classifier enclave and adding one classifier round trip to normal
latency (with additional delay on retry). A flagged score at or above threshold
returns 403. Ordinary classification errors after one retry return 503 and do not
forward. Blocking reveals the per-request decision to the requester by design;
response sizes and timing may also reveal it to network observers. Responses are
not classified before release in this mode.

Classifier verification must succeed before startup admits chat traffic. Later
attestation failure permanently closes this boot's admission gate with 503. Recovery
requires a restart and successful startup verification, with no mock fallback.
Processing deadlines prevent late results from revising signed periods
or forwarding requests after block-mode timeout.

Tinfoil mode aborts startup if classifier verification fails. Its container readiness
probe requires the loopback health endpoint to return HTTP 200 and `status=ready`;
process existence does not establish verification. Local mode permits an unavailable
503 endpoint for diagnostics after failed startup verification.

## Report identity and continuity

An Ed25519 signing key and random boot identifier are generated in memory each boot.
Fetching the public key over the verified channel links it to that application boot;
a signature alone does not prove this linkage. Artifacts must agree on boot and policy,
and restarts during fetching require a fresh coherent fetch. Canonical signatures,
exact schema, counter invariants, and sequence/time ordering must be verified.

Reports use admission-time, non-overlapping periods, including empty periods, and
finalize only after bounded processing. Sequence starts at 1 each boot. Reports are
retained only in bounded memory. Available wall-clock timestamps are not trusted-time
claims. Restart changes both key and boot id and may lose uncollected evidence.

An optional auditor-local checkpoint records the last verified identity, policy, key,
sequence, and period end. It can warn about observed changes, gaps, receding history,
and overdue reports. First-use earlier continuity is unknown. Warnings indicate
incomplete evidence, not proof of wrongdoing. The checkpoint does not recover lost
reports, prove completeness, or detect forks/restarts never seen by that auditor.

## Deliberately deferred protections

See [DEFERRED.md](DEFERRED.md) for trusted coverage controls, verifiable/token sampling,
persistent keys, classifier hardening, output privacy, durable collection, stronger
rollback/fork protection, and other excluded features. No version 1 claim assumes
these protections already exist.
