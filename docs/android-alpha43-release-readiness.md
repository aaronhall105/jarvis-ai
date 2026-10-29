# Android alpha43 release preparation / approval boundary

Date: 2026-10-29. Preparation only; no merge/tag/signing/publication/OTA promotion,
APK installation, host deployment or autonomous activation is authorized here.

## Source and version decision

Base: canonical `602afa31276016999724fcf54858cc09ec67718a`, the merged PR104 tree.
Worktree: `/home/aaron/jarvis-android-alpha43-recovery-release-worktree`.
Branch: `jarvis/android-alpha43-recovery-release`.

| Identity | Version name | Code | Status at audit |
| --- | --- | --- | --- |
| Published / installed Samsung | 19.0.0-alpha40 | 190420 | Published; freshly read from device |
| PR98 recovery | 19.0.0-alpha41 | 190430 | Reserved, open/unmerged; unchanged |
| Documented feature-return plan | 19.0.0-alpha42 | 190440 | Provisional reservation; avoided |
| This maintenance candidate | 19.0.0-alpha43 | 190450 | Proposed; above all three |

Remote semantic tags/releases end at alpha40; no alpha41/42/43 tag exists at audit.
PR98 remains at ac3f3f1e2ac442fa4ded96d1229e51481b4b6636 and PR99 remains at
a586125786fa7971138a9cbd1bb72981f059eb5d. Neither is modified or implicitly approved.
Recheck tags/reservations immediately before any future release authorization.

Only release identities/assertions, release notes and continuity regression tests
change from the base. All merged PR100–104 implementations remain intact. The
baseline's product release and `bridge/app/version.py:JARVIS_RELEASE` must match
Phone/Wear/JarvisVersion for the existing workflow. This is metadata, not a Core
API change: `CORE_APPLICATION_VERSION=3.7.0`, realtime protocol 2, gateway code and
shared Wear protocol remain unchanged. No running Core/gateway redeployment.

## Fresh physical identity, not candidate acceptance

Authorized Samsung SM_G996B was connected as
`adb-RFCR10C1K2H-qFUPIw._adb-tls-connect._tcp` on this date. Read-only user-0
package query reports alpha40/190420, installed/enabled, first installation
2026-08-30 00:22:22, last update 2026-10-25 07:49:27.
A fresh pull of the installed application APK (not data) was checked with SDK36
aapt/apksigner:

- Package: `com.aaron.jarvisvoice`.
- APK SHA256: `d30d92894ca2266676468aa2c589ac55dd0388e5e314c7e3634aadb956f17482`.
- Certificate SHA256: `009fd523f27cf94eb98917e17670804897e6378e5eccf1ce3ead680721691aac`.

This matches the independently verified published alpha40 Phone artifact from
tag v19.0.0-alpha40, source d4013a0137a5b88e3e401627084b35d58c8c813a.
Published Wear APK hash is
`79177ae2eaab91f7a29ee375fb2d70c87ae9b8ccf0e2fbbb5434b9d4fa16d4ee`, with the same
package, version and production signer. No Wear device is certified by this check.
No screen interaction, data export, installation, settings change or key operation
occurred. Candidate physical acceptance remains NOT TESTED even with ADB available.

## Data-preserving update analysis

Phone and Wear keep the same applicationId/package and production certificate
policy. Candidate code 190450 is greater than installed 190420. Final **signed**
APK certificate/package/code/hash must still be independently verified before
installation; a debug build is never a production replacement.

Source comparison against actual alpha40 and this canonical base confirms:

- SecureStore is byte-identical: Developer token/endpoints, Core/HA connections,
  preferences/encryption behavior and Keystore alias `jarvis_voice_token_v1710`.
- ChatHistoryStore and TurnRecoveryJournal are byte-identical; no data/schema reset.
- AutonomyControl is byte-identical; existing operator key alias is reused, not
  replaced/exported. Only public enrollment material may leave Android Keystore.
- Phone/Wear manifests are byte-identical: no change to assistant components,
  permissions, default-assistant declarations, data providers or package identities.
- Wear integration/shared protocol source is unchanged; no transport migration.
- PR104 changes clear only the request-correlated confirmed stale Developer
  selection. Valid history, other preferences and queued-task boundaries are
  covered by DeveloperDeletedSessionTest/DeveloperHistoryRenderingTest. PR100's
  activity/history presentation fixes are also inherited, not overwritten.

Seven new release checks pin monotonic identity/reservations, package/signer,
unchanged Core API/protocol, and six storage/key/manifest source digests. Existing
SecureStore migration, chat history, turn recovery, updater and Developer tests
use disposable fixtures. These prove software contracts, NOT actual retained
phone conversations/settings/Keystore after a future installation.

## Signing and publication audit — unchanged workflow

`.github/workflows/android-ota-release.yml` runs only on pushed `v*` tags. It
requires the tag SHA to equal current canonical, checks synchronized identities,
baseline/notes, runs tests/quality/lint, then restores the existing protected
GitHub keystore and signs Phone/Wear. Expected production certificate is pinned
to the fingerprint above and both output APKs must match it and package/version.
No signing secret, keystore or password was accessed during this preparation.

The same run then creates/uploads GitHub Release assets and generates exact-byte
checksums/product and OTA manifests. An alpha candidate promotes
`jarvis-alpha-feed/update-manifest.json`; beta/stable channels are not promoted by
an alpha release. Existing release assets can be overwritten on a workflow rerun
(`--clobber`), so approved tags/artifacts must not be casually rerun or retargeted.
There is **no active artifact-only mode or separate pause between signing and
publication**. A tag authorization must cover the complete publication/promotion
sequence, not merely obtaining an APK. PR99's alternative remains unmerged/inactive.

Ordinary branch push/PR triggers protected tests and CodeQL, not signing/release.
No workflow or security gate is changed here. Future authorization must first
require exact PR/merged-head checks, security review and clean canonical source.

Phone update workers may notify/download according to settings but cannot silently
install: UpdatesActivity's explicit Install action invokes ApkInstaller's Android
package-installer intent and unknown-source permission flow. Wear has no automatic
GitHub OTA install path. Publishing still makes the artifact available to devices
and can trigger notification/download, so no feed may be promoted during preparation.
Product-manifest source fields describe build provenance, not evidence that the
running Core was deployed from the Android release commit.

## Recovery requirements

PR98's alpha41/190430 is LOWER than this candidate, so it is not an installable
data-preserving rollback from alpha43. Do not merge it blindly over the newer
canonical gateway. The alpha42/190440 return plan also cannot follow alpha43.

If alpha43 needs recovery, prepare known-good Android source in a separately
reviewed branch retaining current gateway/security changes. Select an unused
release identity with code **greater than 190450 and every then-published/reserved
code**, keep the same production certificate/package and compatible storage/key
contracts, run protected validation, and obtain explicit signing/publication and
device-test approvals. Any subsequent feature return needs a still-higher code.
Prefer no migrations; unknown data compatibility blocks release. A signed,
verified recovery artifact and live retained-data rehearsal do not yet exist.
Never uninstall, clear data, downgrade, rotate keys or restore stale databases.

## Separate acceptance tracks and next authorization

**Developer maintenance:** gateway PR104 already deployed at 602afa3. This Android
candidate delivers the UI half, compatible with the same auth/response vocabulary.
After separate approved signing/publication and installation, test selected-deleted
chat handling, valid histories/settings/auth, reconnects, honest activity/error
display, normal chat/voice/Home and Wear behavior. Record user-confirmed retained
data/default-assistant settings and unchanged Keystore identity without exporting
secrets. Do not count current source tests as these physical results.

**Autonomy Phase1:** still uncertified. Guardian remains STOPPED/trusted and
observer disabled by default. Live authenticated observe-only opt-in, grounded
dashboard/roadmap/capabilities, real operator-public-key enrollment, signed STOP,
replay/tamper/RESUME-denial and essential-function/data-preservation acceptance
remain separate gates. No Phase2 authority. Full autonomous rollback proof belongs
to Phase5; this maintenance candidate does not quietly redefine Phase1 scope.

Next checkpoint is explicit exact-head release approval after the preparation PR
and security gates pass. Approval must distinguish merge, canonical tag/signing/
publication/alpha-feed promotion and device installation. None is authorized now.

## Validation evidence

Local full Python suite: **2234 passed, 3 skipped, 143 subtests** (Python 3.14.4).
The three skips are existing opt-in deployment/rollback tests; no production
deployment tests enabled. Gateway subset includes 200 tests and all merged recovery
regressions. Protected Python 3.12 evidence must come from exact-head CI.

| Local check | Result |
| --- | --- |
| Phone tests (Developer deletion/history, preferences, updates, recovery included) | 286 passed |
| Shared protocol / Wear tests | 30 / 1 passed |
| Phone and Wear debug/release lint, debug and unsigned release compilation | PASS |
| SDK36 unsigned release inspection | Both package/name/code correct, non-debuggable, unsigned; compiled recovery/protocol markers present |
| Ruff / format | PASS; 264 Python files formatted |
| Mypy CI target set | PASS; 117 source files |
| Compilation / product baseline / diff whitespace | PASS |
| Bandit active source and release generators | PASS (repository high-severity policy) |
| Dependency audit | No known vulnerabilities |
| Pinned actionlint / Gitleaks current-tree scan | PASS / zero findings |
| Assist streaming / conversation closure / integrity / packaging | 6 / 6 passed; integrity and package generation PASS |

Local unsigned Phone SHA256:
`f0fcae2eebeff057241a399b1424bf86f77cbf630fa8259eed37f93549e88fd7`.
Local unsigned Wear SHA256:
`2f80d77aaf21fbb88ad6fa4133f7c7caa6a8288c5f867d9bc4d89981f03805cb`.
These are local validation artifacts, not signed or install-authorized releases.
All JARVIS_SIGNING_* build inputs were unset for unsigned release builds. Debug
builds use only normal development signing and are not production substitutes.

Reproduce from this worktree with repository dependencies, JDK17 and SDK36:

```sh
PYTHONPATH=bridge python -m pytest -q bridge/tests tools/tests developer_gateway/tests
python -m ruff check bridge/app bridge/tests developer_gateway tools
python -m ruff format --check bridge/app bridge/tests developer_gateway tools
python -m mypy bridge/app developer_gateway tools/generate_ota_manifest.py tools/generate_product_release_manifest.py tools/verify_product_baseline.py
python tools/verify_product_baseline.py --skip-ref
python -m bandit -q -r bridge/app developer_gateway -lll
python -m bandit -q -lll tools/generate_ota_manifest.py tools/generate_product_release_manifest.py
python -m pip_audit -r bridge/requirements.txt -r requirements-improver.txt -r developer_gateway/requirements.txt --progress-spinner off
```

From `android/jarvis-voice-client`, after the unchanged offline-asset preparation:

```sh
env -u JARVIS_SIGNING_STORE_FILE -u JARVIS_SIGNING_STORE_PASSWORD -u JARVIS_SIGNING_KEY_ALIAS -u JARVIS_SIGNING_KEY_PASSWORD ./gradlew --no-daemon :wearprotocol:test :app:testDebugUnitTest :app:lintDebug :app:lintRelease :app:assembleDebug :app:assembleRelease :wear:testDebugUnitTest :wear:lintDebug :wear:lintRelease :wear:assembleDebug :wear:assembleRelease
```

Gitleaks uses the unchanged CI digest
`sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f`,
`detect --source=/repo --no-git --no-banner --redact --verbose=false`, on a
disposable tracked/new-source snapshot excluding ignored build output/credentials.
Exact-head protected CI and CodeQL results are recorded in the PR completion
evidence, distinct from these local results. No production signature or physical
candidate success may be inferred from either source tests or unsigned builds.
