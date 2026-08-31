# Jarvis Android OTA releases

The unified OTA pipeline publishes production-signed Android Phone and Wear OS
artifacts from approved immutable tags. Mutable OTA state is stored only as a
validated GitHub Release channel asset; there is no OTA development branch.

## Current release policy

Development occurs only on `jarvis/unified-production`. A release is triggered
by an approved `v*` tag, and the workflow fails unless the tag commit exactly
equals the current `origin/jarvis/unified-production` head.

The tag-triggered `Jarvis Android OTA release` workflow:

1. verifies product-baseline, version, branch/ref, and clean-source invariants
2. runs Core/tooling tests, quality/security checks, Phone/Wear tests, and lint
3. builds Phone and Watch once from the same source revision
4. restores the existing protected production keystore and verifies both APK
   certificate fingerprints
5. verifies package, versionName, monotonic versionCode, manifests, deep links,
   compiled product markers, resources, and shared protocol components
6. computes checksums from the exact APK bytes that will be published
7. generates `product-release-manifest.json` and `update-manifest.json` with
   exact source and artifact provenance
8. publishes the immutable release assets
9. advances eligible release-hosted channel manifests only after every prior
   validation succeeds

Phone and Watch for one product release must resolve to the same source SHA.
Ordinary branch pushes cannot publish APKs or advance OTA metadata.

## Trusted distribution

Production builds read public HTTPS manifests attached to dedicated GitHub
Release channel endpoints:

- `jarvis-alpha-feed/update-manifest.json`: stable, beta, or alpha
- `jarvis-beta-feed/update-manifest.json`: stable or beta, never alpha
- `jarvis-stable-feed/update-manifest.json`: stable only

Each manifest identifies one validated Phone APK and includes version name/code,
SHA-256, byte size, channel, release notes, publication time, minimum
SDK/protocol, source commit, and immutable release tag. The client rejects
non-HTTPS downloads, malformed metadata, older/equal versions, package/version
mismatches, checksum failures, and a signing identity different from the
installed Jarvis application.

Channel manifests are generated from an approved release and hosted as release
assets. Do not create or restore an `ota-feeds` development branch.

## Production signing

The private keystore and passwords are supplied only through protected GitHub
repository secrets:

- `JARVIS_SIGNING_KEYSTORE_BASE64`
- `JARVIS_SIGNING_STORE_PASSWORD`
- `JARVIS_SIGNING_KEY_ALIAS`
- `JARVIS_SIGNING_KEY_PASSWORD`

The public expected production certificate SHA-256 is:

`009fd523f27cf94eb98917e17670804897e6378e5eccf1ce3ead680721691aac`

Phone and Watch must both match this fingerprint. A keystore, private key,
password, or signing environment file must never be committed or attached to a
release. Keep protected primary and independently verified recovery copies of
the production key.

## Current release process

1. Develop on a short-lived branch created from `jarvis/unified-production`.
2. Update `versionName`, monotonic `versionCode`, `JarvisVersion.RELEASE`, Core
   product release identity, tests, and release notes consistently. Change the
   Core application version or realtime protocol only for a real compatibility
   change.
3. Merge through branch protection and wait for exact-head CI and CodeQL.
4. Confirm the unified worktree is clean and exactly matches origin.
5. Deploy Core and Developer from the approved revision when the release is
   intended to represent a complete product revision.
6. Create and push an approved immutable semantic `v*` tag at that exact head.
7. Wait for the release workflow to test, sign, inspect, publish, and advance
   channel metadata.
8. Download the published assets and independently confirm Phone/Watch hashes,
   package/version, signing certificate, product source SHAs, release tag, and
   byte-identical OTA channel manifest.
9. Install Phone and Watch in place and record physical validation when devices
   are available.

Never manually advance a feed to an artifact that failed this pipeline.

## Historical Alpha14 to Alpha15 signer migration

This section is historical recovery context, not a current installation
procedure.

Alpha14 had no updater and its private signing key was unrecoverable. The
Alpha14 certificate was:

`c7e4a9d80a18c0ae8426b9f2a1befbdbc42d79229ccac936c7504417f11d216e`

Because Android cannot install a differently signed APK over an existing
package, the original test device required one explicitly approved migration
to Alpha15 after preserving recoverable settings. Alpha15 established the
current production identity shown above and introduced the updater bootstrap.
No current release should repeat that migration or generate a replacement key.

## Rollback and recovery

Android normally refuses a lower versionCode. A data-preserving recovery build
must use known-good source, a new version name, a versionCode higher than the
faulty release, the same production signing identity, and the complete release
validation pipeline. Uninstall/reinstall is a last resort because it can erase
application data and settings.

## Safe testing

Unit tests use local fixtures or mock HTTP servers and do not mutate production
feeds. Production has no UI for arbitrary manifest or APK URLs. Installer tests
must preserve Android's explicit unknown-app-source approval; release code must
not bypass package, version, checksum, or signer verification.
