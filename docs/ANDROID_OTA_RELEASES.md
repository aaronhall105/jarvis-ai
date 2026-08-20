# Jarvis Android OTA releases

Jarvis 19.0.0-alpha15 is the one-time updater bootstrap. Alpha14 has no updater and cannot acquire OTA support remotely. The original Alpha14 signing key was lost, so the test device required one approved, backed-up uninstall/reinstall migration to Alpha15. Other Alpha14 installations likewise cannot update in place to the permanent Alpha15 identity. Preserve recoverable settings before migration and never uninstall casually because uninstalling deletes app data.

## Trusted distribution

Production builds read only these public HTTPS feeds from the repository's `ota-feeds` branch:

- `feeds/alpha.json`: stable, beta, or alpha; newest eligible release wins
- `feeds/beta.json`: stable or beta, never alpha
- `feeds/stable.json`: stable only

Each feed identifies one validated GitHub Release APK and includes its version name/code, SHA-256, byte size, channel, release notes, publication time, minimum SDK/protocol, commit SHA, and tag. The app rejects non-HTTPS downloads, malformed metadata, an older/equal version, package/version mismatches, and signing identities that do not match the installed Jarvis package.

Only the tag-triggered `Jarvis Android OTA release` workflow can update feeds. It tests first, builds once, signs that build, verifies it, generates the checksum and manifest from those exact bytes, publishes those same bytes, and only then advances eligible feeds. Ordinary pushes never become OTA releases.

## Stable signing identity

GitHub Actions must contain all five repository secrets:

- `JARVIS_SIGNING_KEYSTORE_BASE64`: base64 of the stable keystore (single line)
- `JARVIS_SIGNING_STORE_PASSWORD`
- `JARVIS_SIGNING_KEY_ALIAS`
- `JARVIS_SIGNING_KEY_PASSWORD`
- `JARVIS_SIGNING_CERT_SHA256`: lowercase certificate SHA-256 fingerprint without separators

The keystore/private key/passwords must never be committed. Losing this key prevents normal in-place updates. The installed Alpha14 certificate was `c7e4a9d80a18c0ae8426b9f2a1befbdbc42d79229ccac936c7504417f11d216e`, but its private key proved unrecoverable. That identity cannot sign Alpha15. The permanent replacement below was therefore introduced through the explicitly approved one-time data-preserving migration; it is authoritative from Alpha15 forward.

The permanent Jarvis Android signing certificate established with Alpha15 is:

`009fd523f27cf94eb98917e17670804897e6378e5eccf1ce3ead680721691aac`

All Alpha15 and later alpha, beta, stable, and recovery APKs must use this identity. Build machines and CI receive the keystore and credentials through the five secrets above; no private-key path or password belongs in Git. Maintain a protected primary copy and a separately stored, verified backup. Never regenerate the key merely because a build machine changes. Losing it would force another uninstall/reinstall migration.

## Publishing Alpha16 and later

1. Develop the release and bump Android `versionName`, monotonic `versionCode`, `JarvisVersion.RELEASE`, and product release identity consistently. Do not bump Core application or realtime protocol versions without an actual compatibility change.
2. Add release notes and deterministic tests. Test a fixture manifest or mock server; production has no configurable arbitrary feed.
3. Push normally and wait for branch CI.
4. Create the approved tag `v19.0.0-alpha16` (or a beta/stable semantic tag) on the exact validated commit and push the tag.
5. The OTA workflow revalidates, signs, verifies, publishes the GitHub prerelease/release, then advances channel feeds.
6. Confirm the release APK digest, manifest digest/size/commit/tag, certificate fingerprint, and successful workflow before announcing availability.

Do not manually advance a feed to a build that did not pass this pipeline. A failed test, signing step, build, or verification leaves feeds unchanged.

## Rollback and recovery

Android normally refuses a lower `versionCode`, so Jarvis does not offer a destructive one-tap downgrade. For recovery, build the previous known-good source with a new version name and a versionCode higher than the faulty release, sign it with the same stable key, validate it normally, and publish it as a new recovery release. This preserves app data. Uninstall/reinstall is a last resort and may wipe settings.

## Safe testing

Unit tests use local JSON fixtures and never contact GitHub. Debug development may inject dependencies in test code or use a local mock HTTP server, but there is intentionally no production UI or resource that accepts arbitrary feed/APK URLs. Installer permission tests should confirm `ACTION_MANAGE_UNKNOWN_APP_SOURCES`; Android still requires explicit user approval for installation.
