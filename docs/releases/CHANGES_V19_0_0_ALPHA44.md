# Jarvis 19.0.0-alpha44 — recovery candidate, not released

Phone/Wear versionCode: 190460. Package: `com.aaron.jarvisvoice`.

This is a forward-version rebuild of the published Alpha43 Android implementation
(`4812ea7a2b83188cedfdf7c3631c70db2ca422a8`), not a new feature or a reversion to
Alpha39/41. All 319 tracked Android files are identical after normalization of
the five version metadata/assertion files. Preserve the production signer
`009fd523f27cf94eb98917e17670804897e6378e5eccf1ce3ead680721691aac`.

No storage migration, preference reset, Keystore alias change, authentication
change, package change, shared-protocol change or permission change. All merged
Developer client repairs and the observe-only dashboard remain. Core application
version and realtime protocol are unchanged; synchronized product metadata does
not authorize any host deployment. Guardian must remain STOPPED.

Intended use is a separately approved, same-key, higher-code recovery rehearsal
of the existing known working Android code. It cannot repair corrupted user data
or an unknown defect already present in Alpha43. Software equality is not proof
of retained physical data or universal essential-function acceptance.

No release tag, signing, publication, OTA promotion or installation is authorized
by this candidate. Use the reviewed artifact-only workflow only after canonical
merge, exact-head checks, protected approval-environment and encryption-recipient
setup, and explicit signing authorization. Never uninstall, clear data, downgrade,
replace keys or restore old application databases. See
`docs/autonomy-phase1-consolidated.md` for the evidence and approval boundaries.
