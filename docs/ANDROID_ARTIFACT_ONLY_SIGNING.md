# Reviewed artifact-only Android signing — inactive proposal

This change adds a separate, manually dispatched **artifact-only** path inside
the existing trusted GitHub signing architecture. It does NOT change the current
tag/publication workflow, credentials, public release, alpha feed, installed apps,
Core, Developer gateway, Guardian or broker. Merging this source does not dispatch
it. No signing operation or approval-environment configuration has been performed.

## Boundaries and source policy

The workflow is `.github/workflows/android-artifact-only.yml`; policy and SDK
orchestration are in `tools/artifact_signing.py`. It accepts ONLY `source_sha`
and `reviewed_pr`. No publish mode, arbitrary command, image, destination, service,
artifact URL, feature ref or environment name is accepted.

Source remains **canonical only**, matching the existing approved release trust
boundary. At dispatch, after building, after approval and before final upload:

- repository ID 1309251679 / aaronhall105/jarvis-ai;
- manual workflow_dispatch on jarvis/unified-production, GitHub-hosted runner;
- exact lowercase SHA equals dispatch SHA, workflow SHA and current canonical head;
- supplied PR must be merged into canonical and its actual squash equals that SHA;
- all four protected check contexts must remain required for everyone and pass
  on both exact PR head and exact canonical source, from the expected Actions app;
- newest relevant PR CodeQL analysis must succeed with zero findings, match the
  source tree exactly, and have no open PR alerts (not just a green analysis job);
- expected operator approval environment must exist with protected-branch policy
  and ONLY Aaron's fixed user ID 291914934 as required reviewer.

Missing/unreadable/truncated/stale evidence denies staging/signing. Additional
checks are not disabled. Existing unrelated default-branch Core findings are not
silently dismissed: this gate verifies the exact reviewed PR's security result.
No signing directly from unmerged PR98 or a feature branch is permitted.

This means the recovery/return sequence must be approved BEFORE replacing the
canonical source with a recovery merge. A production-signed pre-merge candidate
would require a DIFFERENT, separately approved trust-policy change; it is not
smuggled in as an artifact-upload option.

## Build / approval / signing separation

1. Build job checks out the exact canonical workflow/source SHA with persisted
   Git credentials disabled. It validates metadata, prepares wake assets and runs
   Phone/Wear/protocol tests, release lint and UNSIGNED release builds. No signing
   secret is referenced or supplied to this job; known Gradle signing inputs are
   explicitly unset. Fixed-version SDK/JDK/Gradle setup actions are commit-pinned.
2. Only phone.apk, wear.apk and unsigned.json may cross to the second job. They
   are uploaded as an immutable, run/attempt-specific Actions artifact. Its exact
   artifact ID and unsigned-manifest SHA256 are bound through job outputs.
3. The signing job waits for `jarvis-artifact-signing` required-reviewer approval.
   Its visible name contains the requested SHA. An approved record from Aaron for
   that exact run/environment is also required by the script. An unconfigured
   environment, a denied/ambiguous history or admin bypass without approval is
   insufficient. The native environment gate must apply on every job attempt.
4. A separate fresh GitHub-hosted runner downloads only the same run's exact
   artifact ID. It verifies manifest hash, exact file set, source/version, APK
   hashes, package, non-debuggable status and absence of an existing signature.
   Symlinks, hardlinks, directories, oversized files and unknown members fail.
5. Only then are the EXISTING repository signing secrets used by the trusted
   script/SDK. No Gradle, APK code or candidate build script executes in the
   credential-bearing step. SDK signing receives only the required password
   environment; other subprocesses receive no GitHub/signing credentials.
6. A 0600 temporary keystore exists only outside artifact directories and is
   removed on both success and failure. No password/private key is printed or
   included in a manifest. SDK errors are reduced to a non-secret failure.
7. SDK apksigner independently verifies each output and exactly one expected
   production certificate. aapt checks package, versionCode/versionName and
   non-debuggable status. Source/check/approval evidence is rechecked before the
   output manifest is created. Uncertainty means no successful artifact upload.

Expected package: `com.aaron.jarvisvoice` (Phone and Wear).
Production signer SHA256:
`009fd523f27cf94eb98917e17670804897e6378e5eccf1ce3ead680721691aac`.
Phase1 release identity follows the existing 19.0.0-alphaN / 190020+10N policy.
Other release families require a reviewed policy update, not a guessed code.

## Why this cannot promote a release

- Only manual dispatch; no tag/push/pull_request trigger or workflow_run chaining.
- GITHUB_TOKEN permissions are contents/actions/checks/pull-requests/security-events
  **read only**. No PAT, OIDC, deployment or repository-write permission.
- No `gh release`, git push/tag, feed generator, release upload, deployment script,
  service restart, Docker socket, ADB command or device-install step exists.
- The protected environment uses `deployment: false`: required approval remains,
  but even GitHub deployment records are not created by this job.
- Output is an immutable Actions artifact, retained 30 days, containing exactly
  the APK pair and `artifact-only-manifest.json`. The manifest has no OTA URL or
  channel, and explicitly states publicationAuthorized=false and
  installationAuthorized=false. It records source/tree, PR, check IDs, CodeQL
  analysis, workflow/run/attempt, APK hashes/sizes and verified certificate.
- No overwrite, release tag or channel pointer is created. Existing alpha40 feed
  remains unchanged. Actions artifact access/retention is not a public OTA promise;
  retrieve and verify the exact artifact before it expires and before any rehearsal.

GitHub's artifact downloader digest mismatch is only a warning, so it is NOT the
sole integrity gate here. Our bound manifest and APK digests fail closed.
References: [artifact validation](https://docs.github.com/en/actions/tutorials/store-and-share-data),
[environment approval without deployments](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/deploy-to-environment).

## Human setup/activation checkpoint — not performed

### Existing secret-scope boundary

Metadata-only inspection confirms the existing JARVIS_SIGNING_* secrets are
repository-scoped. Their values were not read. The new required-reviewer gate
protects THIS workflow, not every possible workflow an authorized repository
writer could propose. This PR does not move/rotate/export secrets, change the
existing tag publisher, or claim repository-wide isolation. No autonomous account
receives signing credentials or workflow approval rights. Before future unattended
workflow-edit/push authority is enabled, separately review repository secret scope
and protected signing-workflow authority; this Phase1 proposal cannot substitute
for that boundary. Any secret-scope migration requires explicit operator approval
and compatibility review of the existing publisher.

Fresh inspection found **no repository environments configured**. The proposed
workflow therefore refuses to proceed today. Do not auto-create a permissive
environment or move/export/replace signing credentials as a workaround.

After explicit approval of this exact PR and its protected/security checks:

1. Authorize merging the infrastructure PR (not a recovery merge or signing run).
2. Aaron separately configures `jarvis-artifact-signing`: required reviewer ONLY
   Aaron (ID above), protected branches only, and disallow administrator bypass.
   Keep existing signing secrets unchanged. No environment creation is authorized
   by this source PR alone. The script additionally checks approval history so a
   bare bypass/start cannot substitute for explicit Aaron approval.
3. For this single-operator installation, Aaron may request and separately approve
   his own manual run. This is two explicit operator actions, not an automatic
   approval or independent second reviewer. The agent must never approve the job.
   If prevent-self-review is enabled instead, an additional authorized requester
   is needed; do not silently relax that setting.
4. Approve the EXACT recovery and return sequence. Only then merge a freshly
   green recovery PR onto the updated canonical source, recording actual squash.
   No app/runtime deployment follows from this Git merge.
5. Require fresh merged-head checks and matching PR security evidence. Aaron
   authorizes dispatch with that SHA and merged PR number, then explicitly approves
   the pending environment job. No signing run is authorized in this continuation.
6. Independently download and verify signed APKs/manifest after completion. Capture
   alpha40 feed hash before/after to prove it did not change. This runtime proof
   remains outstanding; root-free tests do not certify production signing.

Do not dispatch an older SHA after canonical advances. It deliberately fails
closed. Do not race-cancel the original release workflow or use a draft release
to avoid its unconditional feed-promotion step.

## Recovery and feature-return sequence

PR98 at ac3f3f1e2ac442fa4ded96d1229e51481b4b6636 restores actual alpha39 Android
behavior with alpha41/190430 metadata. Its four protected checks and separate
CodeQL gate passed; analysis 2021791832 on merge tree 4344144ef36b38f14d48a4fea8895de298ad92c8
reports zero findings, no open alerts and no review threads. It is NOT merged or
signed. Recovery removes the dashboard and the intervening PR96 client rendering
fixes; unused signing helper/shared tests remain. Storage and aliases are unchanged.

After this infrastructure merges, refresh recovery against the new canonical
base and rerun exact-head CI/security checks. Approve recovery merge only together
with a reviewed higher-code feature-return plan. Source changes must not remove
the new signer or weaken recovery checks.

Provisional return alpha42/190440 restores the approved alpha40 Android feature
tree (or separately reviewed fixes), preserves storage/Keystore alias and restores
dashboard tests. Its separate PR must explicitly replace the recovery-only
no-dashboard test, not skip it. Version numbers must be rechecked before approval.

Stage and independently verify BOTH same-key recovery and higher-code return APKs
before approving any recovery installation. Canonical sequence may be recovery
merge → stage artifacts → return merge → stage artifacts; neither operation tags,
publishes, changes OTA or deploys a runtime. Each merge/sign/installation boundary
needs explicit approval. The public feed remains alpha40 until separately changed.

On Samsung: record content-free history/settings/auth baseline and real operator
public fingerprint; never export private data/key. After approved in-place recovery,
check retained state/functions. After approved higher-code return, require the
exact same public fingerprint and a new signed STOP proof. A changed fingerprint
FAILS preservation—do not silently re-enroll. Never uninstall, clear, downgrade,
restore old databases or reuse lower-code alpha40 as the return package.

## Samsung / observer acceptance still outstanding

Earlier fresh device hash verified installed alpha40/190420 byte-for-byte, UID
10392, last update 2026-10-25 07:49:27, original install 2026-08-30. Installer label
does not prove who initiated the update. No agent install occurred. Those facts
do NOT establish conversation/auth/Keystore retention. Home/Development launched,
with the truthful unavailable-observer screen. Latest ADB/mDNS reads are empty;
no different device has been paired and no phone state changed.

Reconnect only Aaron's unlocked Samsung: Settings → Developer options → Wireless
debugging. Use its CURRENT connect IP/port (not the pairing port) with local
`adb connect IP:PORT`, then `adb devices -l`. Verify SM_G996B / RFCR10C1K2H and use
the authorized TLS serial. If pairing is needed, Aaron completes it locally; never
send a pairing code or password into chat. No APK installation is implied.

Gateway deployment remains separate: clean exact approved canonical source,
capture prior runtime/unit/config and content-free history state; preserve token,
API credential, Tailscale and data. Install only Developer at a safe tool boundary,
initially observer OFF. Bad/missing auth must return 401; authenticated observer
503 while disabled. Temporary observe-only opt-in requires separate approval and
uses a private separate journal under the existing writable runtime directory:
`/home/aaron/.local/share/jarvis-developer/observer-phase1/roadmap.sqlite3`.
Do not widen systemd permissions or use the manual Developer journal.

Then test authenticated snapshot/observe, STOPPED/trusted UI, honest idle/plans,
grounded capabilities and technical details; invalid auth/envelopes, body limits,
dedup/restart and failure isolation. No engineering jobs or model polling. Run
normal chat/voice/Home/manual Developer checks without controlling household
devices unnecessarily. Compare user-confirmed retained-state baselines privately.

Real enrollment is the separately approved local ceremony in
`docs/autonomy-operator-enrollment.md`: public bundle/fingerprint only, stable
Android Keystore alias, operator root approval. Signed STOP must be confirmed by
host and then refreshed in UI; replay returns stored receipt without re-execution.
Tampered/unknown/expired/stale requests deny. Phase1 API rejects RESUME before IPC;
no live activation test is allowed. Guardian stays STOPPED throughout.

If gateway checks fail, disable observer and atomically restore the captured prior
runtime/unit/config, then restart only Developer and verify source/auth/history/
health. Preserve compatible live data and observer evidence; no database rollback,
credential reset or Guardian reinstall. Unknown outcome requires reconciliation.

**PHASE 1 BLOCKED** pending explicit signing infrastructure/sequence approvals,
verified signed recovery/return artifacts, observer deployment, real enrollment,
signed STOP, essential functions and retained-data recovery proof. No Phase 2.

## Review evidence

Signing infrastructure PR: https://github.com/aaronhall105/jarvis-ai/pull/99.
Initial implementation head b4a488a96bac186968b30ce2549e01878d670fb1:

- local 2164 Python tests, 3 skipped, 143 subtests; 71 new signer tests;
- Ruff/format, 111 active-source mypy files plus signer namespace, compileall,
  Bandit, actionlint and product baseline green;
- staged-source-only Gitleaks 8.30.1 scan: 8.78 MB, no findings;
- protected CI run 40204160719 and CodeQL workflow 40204160648 successful;
- separate CodeQL analysis 2022469244: zero findings, no open PR alerts;
  synthetic merge 0125d1329a2df6b8f05c30a3e093205209542de3 has the exact reviewed
  source tree a6b3091be3cc3feeb95e0102622102c3fa11bd7e;
- real SDK verifier accepted existing unsigned alpha41 Phone/Wear metadata and
  the cached installed alpha40 package/certificate/hash; no signing occurred.

Any later documentation/review commit still requires fresh protected checks.
Use the PR's exact-head completion record, not an earlier success, for approval.
Runtime signing, protected-environment approval and no-feed-change proof remain
NOT TESTED until separately authorized. A green workflow test is not a signed APK.
