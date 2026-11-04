# Local appliance runtime

Run `uv run aiab` from the repository root on Linux. The local listener uses a
self-signed certificate for `localhost` and `127.0.0.1`, generated on every start.
The TLS private key is created in memory and passed to Python/OpenSSL through Linux
`memfd` descriptors exposed at `/proc/self/fd/`. No ordinary temporary key file is
created. Systems without Linux memfd and procfs fail startup; use the Linux container
there. This does not make claims about swap, host root access, or local attestation.
The separate Ed25519 signing key and random boot identifier also change on restart.

Configuration is frozen once per boot. The classifier definition is read once from
the repository/image's `static/classifiers/` directory. The manifest records hashes of that
definition and its complete system prompt, the serialization version, all processing
limits below, and the governed inference origin with credentials removed. Mock
keyword behavior is only a local development smoke check; it does not implement the
full natural-language policy definition or use an LLM generation configuration.

The reviewed processing constants are a 30-second deadline from admission, a
10-second classifier attempt timeout, one retry for ordinary classifier errors, and
a 15-second upstream timeout. The monitor queue holds at most 64 pairs, with two
workers. At most four classifier calls execute concurrently. A timed-out synchronous
call retains its concurrency slot until it actually returns, so a hanging provider
cannot create an unbounded collection of background threads. Supported request bodies
are limited to 1 MiB. These constants are code settings, not environment variables.

Every period is signed after its end plus the 30-second processing allowance, even
when empty. A short reporting period therefore still requires waiting for finalization.
Admission assigns the period; forwarding and classification outcomes stay with that
period. Late results cannot change a signed report or forward an expired block request.
The period schedule uses elapsed monotonic time and timestamps anchored to the boot
wall clock; it does not claim trusted wall-clock time.

Monitor sampling happens before forwarding. Selected upstream failures, queue
exhaustion, and exhausted deadlines each produce one classification error. Monitor
classification runs in background workers after the upstream response is received.
Block mode classifies all supported conversations before forwarding and returns 503
after unsuccessful classification attempts. It returns 403 for flagged results at or
above the blocking threshold. Successful flags below the threshold are forwarded.

All system, developer, user, and assistant text messages, including arrays of text
parts, are serialized together as canonical role-labeled JSON. Streaming, tools,
attachments, non-text parts, and unknown request/message fields are rejected before
admission. Scalar generation settings are type-checked. Provider request and response
text remains transient memory, and operational logging uses static categories/counts.
Audit endpoints return only the approved manifest, public boot identity, finalized
signed reports, or a minimal health state.

Without a key, local mode uses the mock. Setting `TINFOIL_API_KEY` selects the locked
Tinfoil SDK classifier, defaulting to `gpt-oss-safeguard-120b`; no `.env` file is loaded
automatically. The SDK verifies its router before admission. `/aiab/health` exposes
allowlisted verification metadata, and startup logs a static `classifier_verified`
event. Later attestation failure closes the gate for this boot; restart and successful
verification are required for recovery. There is no fallback to the mock. Deployed
serving mode remains a separate integration stage.

The runtime owns the one classification retry, with SDK inference retries disabled.
Generation uses temperature zero, at most 512 output tokens, no streaming, and a
strict JSON schema; these effective settings are in the policy manifest. The full
definition/schema stays in the system message, with all conversation/response data
delimited in the user message. Delimiters do not establish prompt-injection resistance.
The SDK receives a fresh in-memory opaque cache scope for each boot, preventing its
default cache-scope secret file. Provider prefix caching remains possible within that
boot. See [integration evidence and limitations](OPEN_QUESTIONS.md).

`make eval` calls the selected classifier directly over the synthetic corpora and
prints development and held-out metrics separately, including false positives,
false negatives, and errors. Accuracy, precision and recall use successful results;
the capabilities block fraction uses all capabilities examples, including errors.
It also runs the exact two demo prompts and four synthetic request/response pairs.
No labels or split metadata reach the classifier. The output records the policy
digest and requested model. This smoke evaluation is not evidence of robustness;
the evaluator/corpora are excluded from the production image. Live Tinfoil quality
and latency have not been measured by the offline tests.
