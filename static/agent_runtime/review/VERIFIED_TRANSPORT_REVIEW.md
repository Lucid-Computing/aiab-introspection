# Verified DeepSeek transport source review — 2026-11-05

This is implementation-source review and synthetic live qualification, not an
independent security audit or confidential deployment approval. It supersedes the
Python-only compatibility conclusion in RELEASE_REVIEW.md. No parent deployment,
service, sandbox, governance, evidence, UI or container file was changed.

## Reviewed official verification path

The installed official CLI 0.18.5 embeds tinfoil-go v0.15.7. Its
[attestation command](https://github.com/tinfoilsh/tinfoil-cli/blob/v0.18.5/attestation.go)
verifies code/quote/key but does not call the separate legacy hardware-platform
match used by that SDK's SecureClient. An independent credential-free probe of
SecureClient.Verify failed with `no matching hardware platform found`. Thus CLI
success was insufficient to adopt the high-level SDK transport. No hardware match
was removed or policy reconstructed from a failed host.

The broker instead pins stable official
[tinfoil-go v0.16.4](https://github.com/tinfoilsh/tinfoil-go/tree/e1381b71ac15f152b2ab9b2d0f2e2a1805e7e777).
The following source paths were inspected in that immutable module:

- `verifier/document/fetch.go`: bounded HTTPS document fetching with a fresh nonce;
  no credential/body is involved in this public attestation fetch.
- `verifier/verifier.go`: default VerifyV3 authenticates code and platform reference
  values, witness freshness, vendor evidence, quote binding, sealed registers and
  transport key material. The trusted input is `owner/repo@sha256:digest`.
- `verifier/internal/provenance/`: Sigstore identity, artifact, platform endorsement
  and freshness validation with embedded roots. `verifier/internal/quote/` and
  `policy/` apply official platform policy. No options weaken those checks.
- `verifier/client/roundtrip.go`: normal certificate validation followed by exact
  endorsed SPKI key validation in TLS VerifyConnection, before HTTP transmission.
- The high-level client/transport refresh and retry paths were inspected and are
  deliberately not invoked. The broker uses the functional verifier and raw
  TLSBoundRoundTripper directly, with a new pinned verification per sensitive POST.

The release reference is an input to signature verification, then the independently
reviewed digest and TLS key are checked again before constructing each sensitive
request. The request URL is constructed from compile-time host and two allowed
paths. Redirects are refused, POSTs have no rewind function, and credentialed auth
error bodies are discarded. Cancellation kills/reaps the private stdio process;
protocol failures close it permanently. SDK/Go source and transitive dependencies
are immutable module/checksum pins in `verified-transport-pins.json`, `go.mod` and
`go.sum`. Platform endorsement references and signed freshness expiry are retained
in the live verification record. They remain authenticated by official SDK policy,
not locally accepted based on host measurements.

The normalized public verification summary is generated only after VerifyV3
succeeds. Its four success-step labels summarize that one successful operation;
they are not parsed CLI logs or an assertion that a TLS inference connection was
already opened at startup. The per-send TLS key check is enforced by the raw SDK
transport. Attestation alone remains separate from successful inference.

## Serving API interoperability

The actual pinned B200 endpoint serves current vLLM's `reasoning` response/wire
field. The adapter keeps a private canonical reasoning field for Pi and restores
`reasoning` for subsequent inference. Conflicting aliases are rejected. No
reasoning text is retained in public qualification artifacts.

A synthetic, credentialed tool-history `/tokenize` request produced HTTP 400:
`cannot pickle 'pydantic_core._pydantic_core.ValidatorIterator' object`.
This comes from the released chat tokenizer's deep-copy of validated Iterable
tool calls. It is not an attestation or credential failure. Rather than estimate
or omit tool/reasoning tokens, the adapter uses the exact official released prompt
encoder and asks the verified raw `/tokenize` route to tokenize the resulting text.
This is selected consistently for the Go transport, without retrying failed chat
tokenization during agent runs. Upstream source, Apache license, measured release
patch and encoder SHA256 are retained under `verified_transport/`.

The wrapper reproduces text-only normalization, tool insertion, low effort 25,
retained reasoning, DSML argument conversion, tool-result ordering and RFC8785 wire
key ordering. Key ordering matters because tool JSON schemas become prompt text.
Live qualification checks remote chat/raw token-count parity on Unicode text and
reasoning histories with the actual seven tool schemas before any agent inference.
Tool-history chat parity cannot be measured on this release because of the server
bug; exact released source and subsequent actual inference usage bounds support
that path. This limitation should be rechecked against a future reviewed release.

## Failure evidence and limits

Retained runs are explicit failures where applicable:
`go-0157-qualification.json` (legacy SDK verification),
`go-0164-first-inference.json` (invalid search arguments),
`go-0164-tokenizer-tool-round.json` and `diagnostic-qualification.json` (chat tokenizer
bug, including a real seven-tool first turn), and `go-0164-budget-denial.json`
(cumulative token budget exhausted). `go-0164-result-denial.json` retains a
repeat with all seven tools whose final JSON failed the result contract. The
qualification instructions now explicitly restate the exact envelope and fields;
the production parser was not relaxed. None is relabeled as passing. The latest
`live-qualification.json` is authoritative for the final reproducible command.
The older standalone `tokenizer-parity.json` records additional successful remote
parity probes with a minimal tool schema.

Only synthetic Northstar data was sent. The operator credential was read from an
existing file without printing/modifying it, and credentialed inference succeeded.
No inference key needs to be provisioned for this tested operator. No control-plane
mutation, deployment, push, PR, alternative model/provider, allowlist modification,
custom root or verification bypass was used.

The Go-selected transport currently admits one exact B200 host/key/release; key
rotation fails closed and requires renewed review. Python 0.14.0 remains the default
and retains its earlier platform incompatibility. Go 1.27.3/Linux x86_64 and a
prebuilt broker/encoder are new measured-image integration requirements owned by
the parent. Qualification uses an explicit 600-second/262,144-token ceiling to
exercise all seven tools; production defaults remain 180 seconds/32,000 cumulative
tokens. Real findings validation checks structure/references, not independent
semantic entailment. No confidential runtime deployment, receipt signing, remote
client attestation or independent sandbox/semantic-security audit is claimed.

## Final observed qualification

The reproducible command in `verified_transport/README.md` first exited **0** on
2026-11-05 at 17:49 UTC. `go-0164-first-passed.json` records **passed**, actual live
inference, two Pi turns, all seven tools, one reference-validated finding, 11,294
prompt tokens plus 2,153 completion tokens (**13,447 total**). Initial and last-send
attestation summaries both bind release `v0.0.10+b200`, the reviewed digest/key,
platform `tinfoilsh/cvmimage@platform-v0.1.10` digest
`6a4515c3305a81136bda42aef5fc4f13a4a2c63a51c522b17883d008cfbbd472`, and authenticated
witness expiry `2026-11-09T22:20:58Z`. This observed expiry is not a locally extended
lease; every later request must obtain and verify fresh evidence again.

Live parity with the actual tool schemas returned equal chat/raw counts of 702
and 713 for the two probes. That qualified broker SHA256 was
`7eeee63fa8adcefe53c842d8829729afa58234b52e683e63c7a0386dd30e3032`.
No tool outputs, finding text or reasoning are retained in this report.

Local validation passed: 325 workbench/governance tests during integration,
53 focused model/runtime tests after the final adapter changes, the real Node
sandbox harness test, Go broker tests and `go mod verify`. Broker tests exercise
wrong repo/release/key/host/step/expiry before body transmission, real SDK TLS
key rejection before an HTTP handler sees a body, redirects without replay,
malformed/oversized protocol and responses, model/path/stream/token denial, and
sanitized auth failures. Python protocol doubles exercise cancellation/timeout,
process reaping, poisoning, explicit transport selection, credential confinement,
reasoning aliases and exact renderer source pins. Those local tests are not
counted as live inference evidence.

The final binary, including explicit idle-connection cleanup, also passed the
reproducible command at **17:53 UTC**, exit **0**. The authoritative
`live-qualification.json` records two turns, all seven tools, one validated
finding, **11,453 prompt + 2,121 completion = 13,574 total tokens**, both parity
checks passing, and broker SHA256
`437a648d172eb60cc761a1a611a0eb97bdc5b95e08fb99f16e1a3b2d3ae0043e`.
An independent rebuild with the documented Go flags reproduced that binary hash.
The intervening malformed final-result run is retained as a failed attempt;
there are no automatic inference retries in the transport or supervisor.
