# Phase 1 release review — 2026-10-24

## Current decision and authorization

Prepare one controlled candidate containing Android dashboard, optional observer
API and Guardian/broker tooling. Human-directed feature commit/push/PR and protected
CI are authorized. **Canonical merge, production signing/tag/release, gateway
deployment and phone installation require explicit human approval.** No such action
has been performed. Guardian stays trusted STOPPED; observer defaults off. No
autonomous engineering executor exists in this candidate. Phase 2 is not authorized.

Candidate identity is now `19.0.0-alpha40`, Phone/Wear versionCode `190420`, following
the existing +10 policy and alpha39 release history. Core product metadata matches;
Core application/protocol remain 3.7.0/2. These are source changes, not a published
release. Alpha39 remains the public/installed release. No signing workflow changed.

See [the certification matrix](autonomy-phase1-certification.md) for reproducible
current local checks, evidence boundaries and unresolved release requirements.
Earlier counts and the no-PR statement in the historical audit below are superseded
by this candidate review and its exact-head PR check runs.

### Confirmed corrections in this continuation

- Phase 1 HTTP control rejects RESUME before IPC; Android exposes STOP only and
  explains that activation is unavailable. Host operator CLI remains separate.
- Control HTTP failures are not retried through another endpoint; UI refreshes
  authoritative state instead of assuming a transition succeeded.
- API Developer capability requires both configured and healthy evidence.
- A genuinely recurring unhealthy signal reopens WATCHING without duplicating
  its goal; rejected unchanged evidence remains suppressed.
- Gateway installer packages all imported observer modules. Root-free packaging
  regression checks transitive imports without running the installer.
- Disabled-default and observer-startup-failure tests prove manual Developer
  authentication/threads remain independent. No security baseline was weakened.
- Release-identity assertions updated consistently; behavioral tests unchanged.

### Required release sequence (not executed)

1. Commit/push this candidate and open the single intended PR. Require successful
   exact-head `core-tests`, `quality-security`, `android-unit-tests` and `Analyze
   Python`. Complete human release/security review of host tools and Android scope.
2. Obtain explicit approval for canonical squash merge and production release.
   Recheck head/base/protected checks; record actual squash SHA, never infer it.
3. The existing canonical-tag CI is the only approved signer. After separate
   authorization, the immutable alpha40 tag must point at exact canonical HEAD;
   CI validates and signs Phone/Wear, verifies certificate/package/versions and
   publishes release/OTA metadata. This is a public release, not a private signing
   service. Do not revive historical workflows or provide keys to development.
4. Obtain explicit scope for observe-only gateway wiring. The production gateway
   currently lacks these routes; a signed APK alone cannot complete dashboard
   acceptance. Preserve token/API credentials/history/Tailscale. Observer remains
   off by default; any temporary opt-in must be expressly approved and Guardian
   remains STOPPED. No Core runtime deployment is needed merely for metadata.
5. Before approved installation, reconnect Samsung and compare installed package,
   signer and code against the exact downloaded signed artifact and manifest.
   Verify package `com.aaron.jarvisvoice`, existing signer and increasing code.
   Obtain installation approval; use in-place update only, never uninstall/clear.
6. Physically verify dashboard, essential assistant functions and retained state;
   export only the real Keystore public enrollment request. Aaron performs the
   separate reviewed local enrollment ceremony with fingerprint confirmation.
   Test signed STOP while remaining STOPPED. RESUME is denied in Phase 1; test
   its cryptographic contract in an isolated harness, not by live activation.
7. Prove a same-key, higher-code recovery build of known-good product code using
   the same release gates; retain application data and Keystore. Documentation
   alone is not rollback proof. Revalidate retained data after an approved recovery
   rehearsal before final physical certification. Then update this matrix.

### Remaining blockers

PR #97 is now open: https://github.com/aaronhall105/jarvis-ai/pull/97.
The initial e89b603 head passed core-tests/quality-security but received three
CodeQL permission-initialization alerts. A focused source correction creates
incomplete payloads privately before final publication; seven new regressions and
98 focused tests pass. No installed host payload was modified, no scanner was
suppressed, and no merge is authorized by these results. Require fresh exact-head
CI including clean CodeQL alert results, not only the analysis job completing.

Protected CI and human review must attach to the exact feature head. Production
signing/release, gateway wiring and installation await approval. No signed alpha40
or recovery artifact exists. Latest ADB read is empty; historical SM_G996B evidence
does not establish current connectivity. Real dashboard/essential-function tests,
operator enrollment and signed STOP are outstanding. Full Phase 1 certification is
**BLOCKED**, not implied by passing local tests. Later autonomous engineering,
research-provider wiring and privileged release certification remain out of scope.

## Historical signing-policy audit (superseded progress, retained evidence)

## Result

No existing approved feature/PR signing path was found. The current signing flow
requires a canonical merge plus approved immutable release tag. Stop before that
decision; do not manufacture a signing workflow, resurrect retired workflows,
export keys, or merge incomplete R&D merely to get a signature.

No commit/push/PR/tag/workflow dispatch/merge/deployment/install was performed in
this audit. Aaron authorized a feature PR if needed for an existing signing path;
that permission does not change the signing pipeline's canonical-head requirement.
Creating a PR alone would run tests, not produce a production-signed artifact.

## Authoritative evidence

Fresh GitHub API reads confirmed:

- Default/canonical branch `jarvis/unified-production` at
  `429cee8ac2d8efdc5d7c99805699edae78e9a85d`, protected.
- No remote `jarvis/autonomous-development` ref and no open PR to canonical.
- Current workflow directory contains only `android-ota-release.yml`,
  `jarvis-ci.yml`, and `codeql.yml`.
- Latest public release `v19.0.0-alpha39`, published 2026-10-10; previous releases
  alpha38, alpha37, alpha36 establish the existing sequence.
- Alpha39 signing/release run `38068489368` succeeded for tag alpha39 at
  `42cfa630ce4e81f14f73de457dc3c822c53958a8`.

`.github/workflows/android-ota-release.yml` triggers only on `push.tags: v*`.
Before accessing signing secrets, it requires `GITHUB_SHA` to equal the fetched
`origin/jarvis/unified-production` head and requires matching Phone/Wear/Core release
identities and notes. It then runs validation, signs both artifacts in CI using
protected GitHub secrets, verifies the public production certificate, generates
hashes/provenance/OTA manifests, publishes release assets and updates eligible
channel feeds. It is not an artifact-only pre-merge physical-test workflow.

PR CI builds debug Phone/Wear APKs; it does not access production signing secrets.
`docs/ANDROID_OTA_RELEASES.md` explicitly describes canonical approved-tag signing.
No source-controlled local protected signing helper was found. Gradle's release
configuration accepts signing environment values, but is not itself an approved
credential broker. Those four local environment settings were absent in the
physical-signing check; no credential filesystem was searched or exposed.

GitHub's workflow registry still lists historical workflows. In particular,
`wear-v1-signed-pair.yml` is absent from the current default branch. Its last
successful run `33280082714` used canonical commit
`9ffd08d27e3694cbe343e5478e60c1b47baad352`. Reading that historical workflow shows
that even its manual-dispatch route checks exact canonical head equality. It
does not supply an approved feature-branch shortcut.

## Versions and artifact state

Phone/Wear: versionName `19.0.0-alpha39`, versionCode `190410`.
Core product identity and `JarvisVersion.RELEASE` also match alpha39.
The documented release sequence increments versionCode by 10; alpha40/190420 is
the next history-consistent candidate, not an issued/reserved release. No version,
tag, release note or OTA metadata was changed. A production release would require
consistent metadata updates and complete policy gates, not relabeling a debug APK.

No new signed artifact exists. The current local WIP APK signer remains different
from the installed production signer. No install was attempted.

## What the current WIP would introduce if approved for release

- Android Jarvis Development entry/screen, evidence presentation, signed controls,
  and Keystore public enrollment export. The entry is visible; not feature-hidden.
- Optional Developer gateway observer API, separate durable planning journal,
  read-only health observations, evidence deduplication and research adapter.
- Guardian/broker source, explicit operator installers/enrollment, systemd unit
  source, fixed broker Core-deployment entry, tests and documentation.
- No Governor engineering executor. Automated coding/PR/merge/release loop is not
  implemented or certified by this Phase 1 slice.

Host installers are explicit; merging source does not reinstall protected host
payloads. Guardian is trusted STOPPED, autonomous account isolated, write requests
fail closed, remote keys unenrolled, and observer defaults disabled unless
`JARVIS_AUTONOMY_OBSERVER_ENABLED=true`. These prevent autonomous engineering.
They are not a claim that this entire local WIP is already safe to release.

Readiness gap: with the observer disabled, the new dashboard API returns 503 (or
is absent in the currently deployed gateway). A signed APK alone cannot complete
the live dashboard acceptance. Approved feature gateway wiring/observe-only setup
is also required; do not silently enable or redeploy it in this signing audit.

## Checks and release decision boundary

Branch protection is strict and requires `core-tests`, `quality-security`,
`android-unit-tests`, and `Analyze Python` (CodeQL). There is no feature commit/PR,
so protected checks for this WIP have NOT RUN. Do not inherit checks from the
production base or call them green.

Preserved local evidence: 107 focused Python tests, 274 Android tests, lint/debug
build, changed-area static/security checks and source Gitleaks passed before this
documentation-only audit. Full current-WIP repository/release gates have not been
completed. No expensive suite was rerun simply to reconfirm a signing-policy block.

## Data-preserving rollback boundary

- Keep Guardian STOPPED; observer can be disabled without deleting its journal.
- Gateway rollback must restore the verified prior software through its existing
  installer while retaining Developer token, API credential wiring and history.
  No such rollback/deployment was executed or certified in this audit.
- Android policy requires a same-production-key recovery build of known-good
  source with a higher versionCode, validated by the full release pipeline.
  Do not promise ordinary APK downgrade, uninstall, app-data clearing, or Keystore
  replacement as rollback. Retain conversations/settings/auth and signing identity.
- Core was not redeployed and needs no rollback for this audit. Installed protected
  Guardian/broker remain independent of source merges; never revert their state,
  audit or credentials with application software.

The next step needs an explicit release-scope decision before any canonical merge
or tag. A dormant Phase 1 candidate must first be reviewed, gated, and have its
observer UI wiring/recovery plan resolved. Alternatively, a new protected
pre-merge signing design needs separate approval; it is not an existing path.

Fresh ADB at the end of this audit returned an empty device list. Earlier physical
Samsung identification/signature evidence remains valid; reconnect is needed
before a later install. Phone unavailability is not the signing-policy root cause.
