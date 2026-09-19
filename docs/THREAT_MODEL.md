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
