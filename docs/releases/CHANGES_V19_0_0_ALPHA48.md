# Jarvis 19.0.0-alpha48 — OTA publication compatibility recovery

Phone/Wear versionCode: 190500. Package: `com.aaron.jarvisvoice`.

Preserves the reviewed approval-signature, evidence-bound improvement and
immutable-publication changes prepared in Alpha47. The failed Alpha47 run
`43086713281` passed build and initial production-signature checks but was
rejected by the publication helper at `signer_report_mismatch` before it
attempted to create a release. The preserved reconciliation state is `prepared`.

This release pins the APK verification Build Tools to 36.0.0 and supports
both `Signer #1` and scheme-qualified (`V2 Signer:` / `V3.0 Signer:`)
apksigner reports while strictly requiring successful verification, exactly
one signer and the existing production signing certificate. New regression
tests reject malformed, missing, multiple or conflicting signing evidence.

No storage migration or preference reset is required. Conversations, settings,
Android Keystore identity, package, signing identity, Wear and Home Assistant
contracts are unchanged. Core API remains 3.7.0 and realtime protocol remains 2.
No Guardian activation, automated development approval or publication is
implied by this candidate.

Production signer SHA-256:
`009fd523f27cf94eb98917e17670804897e6378e5eccf1ce3ead680721691aac`.

Alpha47's tag and failed-run history must remain unchanged. Alpha48 requires
its own reviewed canonical merge, a separately approved tag, and successful
production signing and immutable release verification before the alpha feed
can advance.
