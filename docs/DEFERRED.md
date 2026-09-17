# Deferred (do not build now — keep interfaces open)

These protections remain outside version 1. They come from the design's red-team
review ([plan §10](../aiab-plan.md#10-deferred-do-not-build-now--keep-interfaces-open)).
Where an interface is mentioned, keep it stable so the item can be added later.
The implemented checkpoint and adversarial demonstration illustrate these gaps;
they do not supply trusted coverage, durable history, or formal leakage protection.

**Coverage**
- Seal governed model weights to a measurement that includes the classifier, so use of those protected weights passes the classifier. Colocate classifier inside the inference TEE. This does not cover other models, copies, or hardware; datacenter-wide claims require separate inventory and trusted coverage controls.

**Sample frame**
- Two independent token counters (inference runtime + AIAB), both signed; mismatch is a finding within their established coverage. Explain why the runtime counter cannot itself be bypassed. *(Interface: reporter fields.)*
- Verifiable random sampling from an auditor-committed seed (HMAC or VRF). Sample by token, not request. *(Interface: `sampler.should_sample`.)*
- Auditor canary requests at a known rate; report canary count.
- Report absolute flagged token counts, not only ratios.

**Keys**
- Persistent report signing key bound into attestation user-data.
- Option: run the classifier in a GPU Tinfoil Container with a fine-tuned label-head model instead of a chat model.

**TEE hardening**
- Auditor-owned hardware in the operator's rack.
- Two TEE vendors, compare reports.
- k-of-n signing for code updates.
- Public transparency log (Sigstore Rekor) for measurements and reports.
- Short-lived session keys only.

**Classifier**
- Label-head classifier to reduce instruction-following attacks; evaluate adversarial evasion rather than assuming the architecture eliminates it.
- Ensemble of two different classifier types; report disagreement rate.
- Session-level grouping with bounded in-TEE state and attested deletion.
- Concentration statistic (how many hashed identities produce 90% of flags).

**Reporting**
- Differential privacy noise, minimum cell size, fixed byte-length reports and externally enforced release schedule. Version 1 has periodic reports but no constant-traffic or formal privacy guarantee.
- Durable auditor collection, transparency logs, and stronger rollback/fork protection beyond the optional last-seen checkpoint.
- Population estimates with sampling uncertainty and measured classifier error; token/compute weighting requires additional trusted measurements. Benign flooding can dilute request percentages even though it does not change selection probability at a fixed sample rate.
- Auditor pre-committed queries.
- Sealed evidence buffer with k-of-n threshold release when the flag rate crosses a threshold.

**Enforcement**
- Streaming support in block mode (classify the prompt before streaming starts).
- Block on response content as well as prompt (requires buffering the response).
- Per-tenant policies and thresholds.

**Governance**
- Mandatory open baseline classifier plus operator add-ons on separate report lines, each with a version hash.
- Legal: terms-of-service disclosure, data protection impact assessment per jurisdiction.
