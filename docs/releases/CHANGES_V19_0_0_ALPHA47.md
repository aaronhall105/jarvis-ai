# Jarvis 19.0.0-alpha47 — evidence-bound improvement and approval signatures

Phone/Wear versionCode: 190490. Package: `com.aaron.jarvisvoice`.
Prepared from corrected canonical source `19d4158b627bc781a07bc879aa2095126b681834`.

- Android development approvals canonicalize paths and numeric costs consistently
  with the protected Python broker, fixing reproducible signature mismatches.
- Correction feedback is attributed to recorded action receipts, with ambiguous
  feedback retained for review instead of inventing executed actions.
- Improvement scope requires source/evidence bindings and explicit review.
  Historical-source reevaluation is append-only; existing evidence is preserved.
- OTA publication requires repository immutability, refuses existing releases,
  verifies complete Phone/Wear assets and provenance before advancing a mutable
  channel pointer, and fails closed on uncertain publication outcomes.

No storage migration or preference reset is required. Conversations, settings,
Android Keystore identity, existing package/signing identity, Wear and Home
Assistant integration contracts are unchanged. Core API remains 3.7.0 and realtime
protocol remains 2. Historical release fixtures and published Alpha46 stay intact.

Production signer SHA-256:
`009fd523f27cf94eb98917e17670804897e6378e5eccf1ce3ead680721691aac`.

Artifact-only run 43068653772 verified the corrected base, not an Alpha47 OTA
release. This candidate requires its own reviewed tag-triggered validation and
production signing after separate authorization. It does not activate Guardian,
authorize provider execution or establish the cause of the original live HTTP 403.
