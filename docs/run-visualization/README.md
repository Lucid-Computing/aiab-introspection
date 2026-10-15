# Recorded audit run explorer

Open `index.html` directly in a browser, or publish this directory to a static host.
It is self-contained: no build step, external libraries, fonts, API calls or credentials.

The page embeds the committed `static/demo/audit/evidence/acceptance.json` snapshot
from the October 15, 2026 v0.1.1 live acceptance run. It deliberately remains a
historical record even when the service is upgraded. Its five cases show synthetic
logs, the actual jointly signed plan (including variants), signed report envelopes,
and the distinct input/plan/policy/image/model/release commitments. Downloading the
underlying evidence uses a local browser Blob; no server receives it.

The walkthrough highlights admission, plan execution, separately attested inference,
output validation, and independent client verification. The CPU and inference
confidential-compute boundaries are shown separately. The auditor is outside both.

This is an evidence visualization, not a signing UI or attestation verifier. It
makes no new cryptographic or network checks. The saved acceptance checks are not
portable hardware attestation. The boot-specific report key was originally learned
over verified TLS; offline verification requires an independently trusted identity
anchor. Native logger completeness, classifier robustness and total leakage limits
are explicitly distinguished from the demonstrated claims.

Verification performed: Chrome via Playwright exercised all five cases and all five
walkthrough steps; outputs matched the saved reports, no browser errors occurred,
and the 390px mobile layout had no horizontal overflow. Embedded evidence was
compared with the committed JSON and all five report/plan bindings were checked.

Published: https://aiab-signed-audit-run.surge.sh
