# Local appliance runtime

Run `uv run aiab` from the repository root on Linux. The local listener uses a
self-signed certificate for `localhost` and `127.0.0.1`, generated on every start.
The TLS private key is created in memory and passed to Python/OpenSSL through Linux
`memfd` descriptors exposed at `/proc/self/fd/`. No ordinary temporary key file is
created. Systems without Linux memfd and procfs fail startup; use the Linux container
there. This does not make claims about swap, host root access, or local attestation.
The separate Ed25519 signing key and random boot identifier also change on restart.

Configuration is frozen once per boot. The classifier definition is read once from
the repository/image's `classifiers/` directory. The manifest records hashes of that
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

This milestone supports the local mock only. Configuring a Tinfoil API key or deployed
mode fails explicitly until the reviewed SDK integration is installed. There is no
fallback to mock for unavailable attested configurations. The classifier interface
accepts synchronous or asynchronous implementations; verification failures close the
chat admission gate. The Tinfoil integration must supply explicit verified recovery
before reopening that gate.
