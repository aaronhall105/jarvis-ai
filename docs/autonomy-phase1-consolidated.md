# Phase 1 consolidated certification — 2026-11-01

## Decision: BLOCKED, not Phase 2 authorization

This record supersedes the pending Alpha40/unenrolled/disconnected statements in
the historical Phase 1 matrix. It does not retroactively change those records or
silently mark their physical recovery gate passed. Observe-only software and the
operator-confirmed physical dashboard/STOP checks are distinct from unperformed
retained-data/recovery acceptance. Full automated deployment/rollback belongs to
Phase 5, not this recovery preparation PR.

Verified canonical: `99734d60d2d1029bee4dca092d6f5cb9c3c1b56c`, clean locally and
equal to remote. All four exact-canonical protected checks succeeded:
[CI 41276064947](https://github.com/aaronhall105/jarvis-ai/actions/runs/41276064947)
and [CodeQL 41276064917](https://github.com/aaronhall105/jarvis-ai/actions/runs/41276064917).
Protection remains strict, administrator-enforced, with core-tests,
quality-security, android-unit-tests and Analyze Python required. The 24 historical
canonical findings (21 polynomial ReDoS, 3 log injection) remain open, not dismissed
or represented as zero global findings. Recovery exact-head CI and separate PR
security results must be recorded on its PR after completion.

## Evidence matrix

| Gate | Result | Evidence and boundary |
| --- | --- | --- |
| Trusted STOPPED Guardian | PASS | Fresh CLI and authenticated observer GET agree: STOPPED/trusted, generation `64e7361f-f003-4b88-b619-31a3363a6d11`. No control call made. |
| Host status service / namespace isolation | PASS | Installed socket active; deployed gateway GET retrieves the same trusted generation under unchanged strict sandbox. PR111 regression/CI evidence retained. |
| Observer auth and engineering denial | PASS | Unauthenticated production GET 401, authenticated GET 200, `phase=observe_only`, `development_execution_available=false`. Source defaults off. No production POST/refresh/RESUME sent. |
| Real observations, restart, dedup, honest idle | PASS (isolated live-read probe) | Existing `tools/certify_autonomy_observer.py`: two processes, six real health observations, zero goals, stable roadmap IDs, authenticated reconnect. Journal `/tmp/jarvis-observer-http-phase1-flxplthw`; zero Developer threads/calls and unchanged canonical. No artificial research goal. |
| Live production roadmap | PASS with limitation | GET preserves one prior plan, no current work, seven stored observations, zero research attempts and no improvements. Observations expired and capabilities are unverified; not represented as present failures or verified capabilities. |
| Fresh production capability inventory | BLOCKED | Snapshot evidence exceeds the 15-minute freshness window; observer schedule is six hours. No production evidence refresh or scheduling change authorized here. A separately approved bounded refresh can verify the physical inventory; do not fake freshness. |
| Configured provider without a real response | PASS | Live provider health UNKNOWN/configured=true; healthy=false is not interpreted as a failure. No paid provider request or success claim. |
| Samsung connection and installed identity | PASS | Fresh authorized mDNS device SM_G996B/t2s, package alpha43/190450, enabled, installed, not suspended. APK hash below equals independently verified public artifact. |
| Production signing continuity | PASS (installed Alpha43) | Installed APK hash equals public bytes; SDK36 verifies public APK's production certificate. This is not a signature for the unsigned recovery candidate. |
| Physical dashboard / real signed STOP / broker audit | PASS (reused operator evidence) | Latest authoritative operator handoff explicitly verifies all three. No duplicate STOP, new enrollment or private-key export attempted. This report does not invent a new independently captured transaction ID. |
| Replay, tamper, generation, unknown-key and RESUME denial | PASS (root-free software, not a new physical transaction) | `test_autonomy_operator_contract.py`, `test_autonomy_broker_operator.py`, enrollment/Guardian/status tests. Receipt replay has no second effect; altered requests and Phase1 RESUME fail closed. No live RESUME call. |
| Source/data/key continuity contracts | PASS (source) | Alpha44 preserves all 319 Alpha43 tracked Android files after five metadata substitutions; existing storage/Keystore/manifest hash tests retained. No schema migration or settings reset. |
| Retained conversations/settings/auth and key fingerprint | NOT TESTED comprehensively | Read-only package baseline below supports continuity only. No historical private-data baseline can be reconstructed. User-local comparison and an enrolled public fingerprint comparison are still required for physical recovery acceptance. |
| Essential physical voice/Home/manual Developer functions | NOT TESTED in this continuation | No phone navigation, device commands affecting settings, tool executions or integration mutations. Core/gateway HTTP health healthy; this is not full physical assistant acceptance. |
| Recovery source / private signing | BLOCKED for artifact | Candidate alpha44/190460 prepared; exact-head source certification is on its PR. Approval environment missing; no signed recovery exists. |
| In-place recovery / retained-data proof | BLOCKED | Requires reviewed merge, signing setup/approval, verified signed APK and separate device approval. No install, uninstall, downgrade, app-data restore or clear. |
| Autonomous development/release/rollback Phases 2–5 | NOT TESTED / OUT OF SCOPE | Never activated. This matrix grants no engineering authority. |

## Non-sensitive device and runtime baseline

Samsung serial: `adb-RFCR10C1K2H-qFUPIw._adb-tls-connect._tcp`.
User 0 package `com.aaron.jarvisvoice`, Alpha43 / 190450. First install
2026-08-30 00:22:22; last update 2026-10-29 19:20:33. CE data inode 2733471,
DE data inode 2537817. These metadata support package/data-directory continuity,
not the contents of conversations or credentials. The selected Android assistant
is Google's GSA VoiceInteractionService, not Jarvis; no default was changed.
No running Jarvis PID was reported at this check; that alone is not a crash claim.

Installed/public Phone SHA256:
`7c7017bea346e70766c7f9da72997ae0739adf80c77ef5558d93247078c81313`.
Public Wear SHA256:
`a59b12f7cfc02d6f806f05dd2877c3e9f3ab9cce388f718003c91cfe83845e3a`.
Production certificate SHA256:
`009fd523f27cf94eb98917e17670804897e6378e5eccf1ce3ead680721691aac`.
[Published Alpha43](https://github.com/aaronhall105/jarvis-ai/releases/tag/v19.0.0-alpha43)
was signed at `4812ea7a2b83188cedfdf7c3631c70db2ca422a8`, workflow 40935422998.
Existing independent verification: `/tmp/jarvis-alpha43-public-release-evidence.md`.
No Wear physical acceptance is inferred.

Core healthy at `b09a1de63cb54481972cee8803a5ee6be1b90ed3`; Developer healthy at
`99734d60d2d1029bee4dca092d6f5cb9c3c1b56c`, PID2346089, service active since
2026-11-01 05:32:47 UTC. No service restarted, configuration changed, production
SQLite opened for writes, private conversations exported or credentials printed.

## Recovery candidate and version reservations

Fresh worktree `/home/aaron/jarvis-phase1-alpha44-recovery-worktree`, branch
`jarvis/phase1-alpha44-recovery`, base canonical above. Alpha44/190460 is the next
unused +10 identity after published Alpha43/190450 and reservations Alpha41/190430
(PR98) and Alpha42/190440 (old return plan). All local/remote branch build versions
and remote tags/releases were checked; none exceeded 190450. No open PR existed
before this candidate. PR98/99 are preserved closed historical workstreams, not
merged into this candidate or altered.

Alpha44 restores the **known working Alpha43 Android implementation**, not the old
Alpha39/41 feature-removal tree. A full normalized Android tree digest pins this
claim. It preserves recent Developer fixes, dashboard, operator alias, manifests,
SecureStore, ChatHistoryStore, TurnRecoveryJournal, Wear protocol, HA configuration
and authentication behavior. It is not a cure for an unknown Alpha43 defect or
corrupted data. Its scope is a forward-version known-code rehearsal; no injected
failure or automated rollback is proposed. No feature-return build is required
because no feature is removed; future changes require a still-higher unused code.

## Private signing blocker and exact approval sequence

Read-only API returned zero environments and 404 for `jarvis-artifact-signing`.
Repository is public; operator has admin permission. Nothing was configured.
Current merged `.github/workflows/android-artifact-only.yml` requires exact current
canonical SHA and its reviewed merged PR, all protected checks and the separate
zero-finding PR CodeQL gate. Build has no signing secrets; the approval-gated
signer uploads only encrypted artifacts. Contents/actions/checks/PR/security-event
permissions are read-only; no tags, release publication, OTA, deployment or device
access. Existing public tag workflow is unchanged and must NOT be dispatched.

Required separately approved setup (details: `ANDROID_PRIVATE_SIGNING.md`):

1. GitHub Settings → Environments: `jarvis-artifact-signing`; ONLY reviewer
   `aaronhall105` (ID291914934); protected branches only; administrator bypass OFF.
   Single-operator self-review remains allowed so Aaron can dispatch and separately
   approve; no automatic approval or silent bypass.
2. Operator holds and backs up an encrypted RSA3072–8192 recipient private key
   outside repositories; test encryption/decryption locally. This is NOT an Android
   signing key. No key generation/access is performed by this task.
3. Add only public certificate environment variable
   `JARVIS_ARTIFACT_RECIPIENT_CERT_PEM`, valid at least another day. Independently
   confirm DER SHA256 on the operator machine. Keep all signing secrets unchanged.
4. Approve exact recovery PR merge only after green exact-head CI/security. Record
   actual squash R; require post-merge checks. Canonical must stay at R throughout
   signing. Separately authorize manual artifact-only dispatch from canonical with
   `source_sha=R`, `reviewed_pr=<recovery PR>`, `recipient_cert_sha256=<verified DER
   SHA256>` and Aaron's explicit job approval. Never dispatch a guessed SHA or an
   old feature commit. A failed run needs diagnosis and a fresh approval, not replay.
5. Retain the already verified public Alpha43 pair and both private Alpha44 signed
   artifacts before requesting any device action. Decrypt only operator-locally;
   check authenticated decryption exit status before extracting fixed regular
   files. Verify receipt/run/PR/source, artifact hashes/sizes and manifest, SDK36
   signature/certificate, package, non-debuggable status and exact version190460.
   Retain verified artifacts beyond Actions' 30-day expiry. No public OTA promotion.
6. Obtain separate approval for a bounded observe-only refresh/physical acceptance
   and, separately, same-key **in-place** Alpha44 installation. Immediately before
   installation recheck signer/package/current code and record only booleans/counts
   for retained chats/settings/auth plus public operator fingerprint on the phone.
   No private content export. After update verify those, dashboard, authenticated
   Developer history, normal Home/voice functions and signed STOP while remaining
   STOPPED. Never test RESUME against the live broker. No uninstall/clear/downgrade.
7. Any mismatch halts certification; preserve state and diagnose. Alpha43 cannot
   be installed back over Alpha44 by downgrade. A later corrected recovery would
   need separately reviewed source and a still-higher signed code.

No immediate signing or installation command is safe while environment, reviewer,
recipient, merged SHA and signed artifact verification are outstanding. Approval
for this PR does not approve Phase2, full automated rollback or arbitrary releases.

## Reproduction and evidence location

Local logs: `/tmp/jarvis-phase1-final.FICIaB/`. Full Python tests run against fresh
disposable fixtures; Gradle Phone/Wear/shared tests, debug/release lint and debug
and UNSIGNED release compilation. Release signing inputs explicitly unset.
Ruff/format, mypy, compileall, Bandit, dependency audit, baseline, pinned actionlint
and current-tree Gitleaks follow existing CI commands. Exact final counts and CI
links are recorded in the recovery PR evidence comment, not inferred from prior CI.

Initial local Python run: 2444 passed, 5 failed, 3 skipped, 143 subtests. All five
failures were Guardian installer source-mode checks on the fresh checkout made
under umask0002. Removing group/other write permission on the seven local installer
input files fixes the environment without altering content, tests or installed
protected payloads. Final full rerun is recorded separately; failures are not hidden.
Three existing gated deployment/rollback tests stay skipped, not silently certified.

Final frozen-source local run: **2449 passed, 3 skipped, 143 subtests passed**,
47.81s, Python3.14.4. A preceding rerun loaded the old release-note filename while
it was being corrected to the established `CHANGES_V19_0_0_ALPHA44.md` convention
(2448 passed, one failure); the final run above verifies the corrected source.
Phone **286**, Wear **1**, shared protocol **30** tests passed, zero failures/skips;
all requested lint/debug/unsigned-release tasks succeeded (195 Gradle tasks).
Ruff/format, mypy (121 active files plus signer), compilation, high-severity Bandit,
dependency audit (zero known vulnerabilities), product baseline and diff checks
passed. Pinned Gitleaks scanned 9.09MB with zero findings; pinned actionlint passed
with explicit workflow paths and the invoking unprivileged UID on the read-only
source snapshot. Initial snapshot scanner invocations hit missing `.git` discovery
and UID permissions, not source findings; neither gate was disabled. Assist
streaming/closure 6/6, audio-gate12, integrity and package checks passed.

SDK36 inspected both local UNSIGNED recovery APKs: correct package, Alpha44/190460.
Phone SHA256 `d5ac85a6353884ed97192dacd8836c4b4038e427030d1741b7a80159f211225a`;
Wear SHA256 `267eb02af90182085397d0c425e4146ecba78d260fab6dcd4ded205b595b0b47`.
These are NOT production-compatible signed artifacts and must not be installed.
Authoritative Python3.12 and exact-head protected CI remain separate PR evidence.
