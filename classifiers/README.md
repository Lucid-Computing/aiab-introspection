# Classifier definitions

Add a short UTF-8 Markdown definition beside `ai_capabilities.md`. State what counts,
what does not count, and how uncertainty should affect confidence. Support both a
complete message list plus response (monitor mode) and a message list with no response
(block mode). Use harmless synthetic examples and a stable, versioned classifier id.

The system prompt supplies the JSON output schema. Definition text describes the
policy; it must not introduce additional result fields or require reporting conversation
contents. Mark submitted conversations as untrusted data. This instruction and input
delimiters do not provide a security boundary against prompt injection.

Set `CLASSIFIER_DEFINITION` to the definition's path in the image at build/deployment
configuration time. The appliance loads it once per boot and hashes both its exact
contents and the full system prompt into the effective policy manifest. Deployment
must not mount overrides, edit the file at runtime, or download policy remotely.
Changing a definition requires a reviewed image and independently approved policy
digest. Evaluate held-out cases without tuning on them. Mock keyword results are
development behavior and do not validate a new policy.

