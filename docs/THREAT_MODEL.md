# Version 1 threat model

This document states design requirements and limits. Until implementation and live
verification complete, it is not evidence that a particular deployment meets them.

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

The operator's inference server and webchat are outside the trusted system. The
inference server receives forwarded conversation contents by design. The auditor
must approve image and policy digests independently; trusting values supplied by
the endpoint itself is circular. The policy manifest is derived from effective
startup settings and fetched over the same attested channel. Image identity alone
does not establish environment settings. Debug enclaves must be rejected.

Classifier calls must exclusively use the reviewed Tinfoil SDK verification path.
The requested model identifier is not an immutable weight digest. Which serving
code, routing settings, identifiers, or weights the provider actually attests remains
an explicit open question until checked against its documentation. Local self-signed
TLS and the mock classifier provide development behavior, not enclave guarantees.

## Data handling and observable channels

Request/response text must never be written to appliance disk, logs, reports, or
metrics. Signing keys, local TLS private material, processing queues, and retained
reports remain in memory. Operational logs use allowlisted counts, timings, and
error categories; no raw HTTP/SDK exception text or body debugging. Logging filters
and sentinel regression tests are defense in depth and cannot prove that arbitrary
plaintext or all covert channels are absent.

Expected outbound destinations are the configured governed inference origin, the
reviewed classifier SDK endpoint, and any provider verification infrastructure required
by that SDK, to be inventoried after documentation review. No analytics or arbitrary
request-selected destinations are allowed. Browser traffic goes to the configured
AIAB endpoint and same-origin static assets. Observable outputs include signed reports,
public identity/manifest/health endpoints, HTTP responses and sizes, connection timing,
and allowlisted operational logs. The provider and network may observe traffic metadata.

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
attestation failure closes admission with 503 until verification recovers, with no
mock fallback. Processing deadlines prevent late results from revising signed periods
or forwarding requests after block-mode timeout.

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

