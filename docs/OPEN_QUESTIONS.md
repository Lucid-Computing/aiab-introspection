# Open questions to resolve while building

Write answers here as evidence becomes available. At the skeleton stage all questions
below remain unresolved; no Tinfoil SDK or container behavior has been assumed.

1. In a Tinfoil Container, does the enclave terminate TLS, or does the app? What port does the app listen on?
2. Can a Tinfoil Container keep a signing key across restarts? If yes, how is it bound to attestation?
3. Are outbound connections from a Tinfoil Container to the Tinfoil inference API allowed by default?
4. Which Tinfoil-served models support strict JSON output, and what is the cheapest one that classifies well?
5. Does the Tinfoil Python SDK expose the verified measurement so the AIAB can log the classifier enclave digest at startup?
6. What is the classifier round-trip latency from a Tinfoil Container to the inference API? Block mode adds this to every request.
7. What does classifier attestation bind: serving code, routing configuration, model identifier, and/or immutable weight digest? Document the supported claim; do not equate a model alias with pinned weights.
