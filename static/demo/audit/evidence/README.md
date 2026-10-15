# Live acceptance evidence — October 15, 2026

The deployed v0.1.1 CPU Tinfoil executor used real, separately attested safeguard
inference. The logger was synthetic as explicitly authorized, with real signatures
and exact-byte chains. No production model stub or unsigned-input bypass was used.

- `evaluation.json`: first real run of the three synthetic fixtures; actual confusion
  counts and classifier settings, plus two negative/schema-variation checks.
- `acceptance.json`: second run using a newly signed plan that commits to the first
  evaluation record. Contains signed plans, five signed reports, public identity,
  fixture text and verification results. The final plan is also in `plan/`.
- `rejected-pins.json`: live client rejection of deliberately wrong release/policy pins.
- `attestation-verification.json`: separate Tinfoil CLI verification record, not a
  replacement for independently verifying attestation and approving release pins.

Both inference runs gave TP=2, TN=1, FP=0, FN=0, errors=0. FPR denominator is one,
FNR denominator is two. These are smoke-test counts, not a robust accuracy estimate.
All fixtures are synthetic and safe to share. No credentials/private keys are included.
The report signing key is boot-specific. Live retrieval stops working after restart;
retain the verified identity and reports if historical verification is needed.

Final live checks: valid dual signatures; tampered plan rejection; unauthenticated
request rejection; unsafe program rejection; altered log rejection; idempotency;
benign, prohibited and distributed-evidence classification; invalid-output signed
failure without text; newly signed enum report schema without executor changes.
An independent auditor-role client also retrieves and verifies reports, while run
submission is forbidden for that role. Local tests cover timeout, model failure,
capacity, restart identity, exact context overflow, transport identity/redirects,
malformed outputs and signature/input tampering.

Re-run from `static/` with a fresh prefix and output path:

```sh
PYTHONPATH=. uv run python demo/audit/acceptance.py \
  --host aiab-signed-audit.lucid-computing.containers.tinfoil.dev \
  --repo lucid-computing-labs/aiab-signed-audit-enclave \
  --release-digest sha256:45604e2b43ea985620ee90b95f557a441c5a4770757424dd6c63de36c4476484 \
  --policy-digest sha256:26902b697f28fed39d8d85d0b45ecbf9da510cf70a9067c65efe0e65981e7e82 \
  --trust deploy/tinfoil/audit/trust.json \
  --key-directory /home/jon/.local/share/aiab-audit-demo \
  --evaluation demo/audit/evidence/evaluation.json \
  --run-prefix review-001- --output /tmp/aiab-review-001.json
```

This incurs five real inference calls unless submissions already exist in the same
boot. Do not repeatedly run beyond the documented retained-run capacity. A new
policy/model/dataset requires reevaluation before approving its signed commitment.

Native logger/WORM coverage is deferred until that system exists. General RSI
robustness, appeals, a negotiation UI and formal covert-channel prevention are
outside this baseline. The service has not undergone an independent security audit.
