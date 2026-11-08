# Jarvis 19.0.0-alpha45 — owner-signed development approval

Phone/Wear versionCode: 190470. Package: `com.aaron.jarvisvoice`.

This release carries the reviewed Android owner-approval implementation from
the canonical merge commit. It accepts dynamically scoped, evidence-bound
development tasks while rejecting non-canonical, protected, expired, malformed
or tampered scopes. Existing storage, Android Keystore aliases, package
identity, signing identity, permissions and Wear protocol remain unchanged.
No storage migration or preference reset is required.

The production signer must remain
`009fd523f27cf94eb98917e17670804897e6378e5eccf1ce3ead680721691aac`.
The OTA workflow must build both Phone and Wear APKs, verify that certificate,
and publish only immutable release-hosted assets. No uninstall, data clear,
Guardian activation or autonomous development task is implied by this release.
