# Alpha41 recovery review and Phase 1 approval boundaries

2026-10-25 continuation. Scope: source preparation, local verification, a dedicated
recovery PR and exact-head protected CI. No merge/tag/signing/publication, phone
installation, gateway/host deployment, operator enrollment or autonomy activation.
Guardian must remain trusted STOPPED. This supersedes earlier local-only recovery
status in `autonomy-physical-preparation.md`; physical acceptance is still blocked.

## Candidate and source review

Branch: `jarvis/phase1-alpha41-recovery`, based on the actual alpha40 squash
`d4013a0137a5b88e3e401627084b35d58c8c813a`. Recovery identity: alpha41 / 190430.
Final commit, PR and CI evidence are recorded on the recovery PR, not guessed here.
The protected canonical base was freshly verified unchanged before preparation.

Phone/Wear behavior returns to the actual signed alpha39 source
`42cfa630ce4e81f14f73de457dc3c822c53958a8`, with synchronized forward-version
metadata. Package stays `com.aaron.jarvisvoice`; signer policy stays
`009fd523f27cf94eb98917e17670804897e6378e5eccf1ce3ead680721691aac`.
No signing/workflow changes. No Core behavior, gateway or installed host changes.

One correction to the preliminary patch is necessary: keep alpha40's unchanged
package-private `AutonomyControl.java`, its Java test and shared public signature
vectors. Host enrollment tests also consume those vectors. Deleting them would
break cross-language security coverage. No recovery production source references
the helper, and no dashboard/client/activity/manifest entry exposes controls.
The helper has no static key-creation or network side effects. Three new recovery
tests enforce no entrypoint, exact storage/helper digests, alias continuity,
same package and higher code. Security tests were retained, not skipped/weakened.

All 310 alpha39 Android baseline files remain identical apart from five exact
version-name/code substitutions. The only additions relative to that Android
baseline are the three unchanged contract/helper-test/vector files above.
SecureStore, ChatHistoryStore and TurnRecoveryJournal match alpha39 AND alpha40.
No database migration, app-data restore, credential reset or Keystore deletion.
The MainActivity manifest digest was restored to its exact alpha39 value, not
disabled. Baseline assertions and protected checks remain mandatory.

Explicit tradeoffs for release review:

- Recovery removes the dashboard and also the intervening PR96 Android
  response-rendering improvements. It is the previously released client behavior,
  not a claim to retain every alpha40 bug fix. Manual API Developer server remains.
- Keeping a signing helper in source does not enroll a key or grant authority.
  Only a reviewed, operator-authorized enrollment changes protected host policy.
- No physical data/Keystore continuity claim follows from identical source alone.
- Existing Core security findings are not fixed or suppressed by this recovery.
  Separate exact-PR CodeQL alerts must be checked, not just job conclusion.

## Local validation and review

Full Python, Android/Wear, version/manifest, security and regression results are
recorded on the exact-head PR. These are distinct from protected CI and from
device certification. Existing alpha40 results are not reused as alpha41 CI.

Initial recovery-focused set: 66 passed (including the new recovery invariants,
operator enrollment, product/OTA manifests and baseline). First broad run found
five Guardian tests correctly rejecting group-writable files in the new worktree;
local checkout modes were tightened on the seven exact installer source files.
No source guard was changed and no installed root file was touched. Focused
Guardian rerun: 63 passed. Full suite rerun: **2096 passed, 3 skipped, 143 subtests
passed** (36.62s); one existing Starlette/httpx deprecation warning.

Phone 265 / Wear 1 / shared protocol 30 tests pass with zero failures/errors/skips.
Debug and release lint and debug builds pass. Wake assets prepared using the
existing script and a separate temporary cache. Unsigned release builds passed with
all four production signing environment inputs explicitly unset. Debug/unsigned
artifacts are NOT eligible for physical installation and are not production signed.

Ruff/format, active-source mypy, focused broker/enrollment typing, compileall,
shell syntax, high-severity Bandit, dependency audit, actionlint and baseline
checks pass. Assist streaming/closure/package-integrity checks pass. Source-only
secret scanning excludes private runtime data and generated dependency/build
outputs by exporting only the exact Git candidate tree, not by weakening rules.

Local logs: `/tmp/jarvis-alpha41-validation.pMu8aw/`. CI and security review must
attach to the final exact PR head; no auto-merge is enabled.

## Safest compliant non-promoting signing path — proposal only

The existing `android-ota-release.yml` accepts canonical tags only. Its validated
signed build publishes immutable release assets and then promotes the channel feed
unconditionally. Uploading the workflow artifact occurs AFTER publication. There
is no current supported sign-only or private feature-branch signing operation.

Therefore do NOT trigger alpha41's existing release workflow if the requirement
is to leave alpha40 on the public feed. Precreating a draft release does not stop
the feed step. Racing cancellation between signing/publishing, changing tag names
to evade channel selection, or intentionally failing permissions are not safe
staging procedures. Never export keys or introduce a local/autonomous signer.

Recommended next signing-policy decision: authorize a separate, narrowly reviewed
change to the EXISTING trusted release architecture that explicitly separates
artifact preparation from publication/promotion. It should:

1. Require an explicit human-approved staging request with an immutable exact
   canonical SHA and expected version. Keep canonical-head equality, all protected
   checks, security alerts/reviews and the normal release validation gates.
2. Use the same protected CI signing identity; no secret changes or exposure to
   autonomous code. Signing requires its own human authorization boundary.
3. Stage verified APKs plus immutable source/hash/certificate/package manifests as
   retained CI artifacts, with no release creation, tag mutation or feed writes.
   Restrict staging permissions and test that publication cannot be reached.
4. Keep production publication/promotion a separate explicit authorization with
   exact artifact identity revalidation. Require tests for default non-promotion,
   unknown mode rejection, wrong SHA/version/checks rejection and no side effects.

This is a proposal, NOT implemented or authorized by this recovery PR. Even a
staging mode should not silently sign unmerged/unreviewed feature code. Recovery
merge and any signing-workflow modification require explicit review/approval first.
If the operator chooses the unchanged public-release path instead, explicitly
acknowledge alpha41 feed promotion; it cannot satisfy a no-feed-change requirement.

## Feature-return and retained-data recovery rehearsal

The Samsung already has the verified alpha40 APK. Do not install alpha40 again
merely to repeat this milestone. Original first-install date is not data proof.

Before an approved recovery rehearsal:

1. Complete observe-only dashboard and essential-function acceptance on alpha40.
   Record content-free counts/presence and operator confirmation of conversations,
   settings, endpoints, connected accounts and manual Developer authentication.
   Do not export tokens, private databases, transcripts or signing/private keys.
2. With separate enrollment approval, create/reuse the real Samsung Keystore alias
   `jarvis.autonomy.operator.v1`; record only its public fingerprint/bundle. Enroll
   that exact public key via the reviewed local operator ceremony. Test signed STOP
   while preserving STOPPED. Record the enrolled public fingerprint independently.
3. Have BOTH the verified signed recovery artifact and a reviewed higher-code
   feature-return artifact available BEFORE the recovery installation, plus exact
   approved installation commands/hashes. No assumption that signing can be done
   on demand if recovery removes the dashboard.
4. Provisional return identity is alpha42 / 190440 only if still unused at review.
   Build it from the exact approved alpha40 Android feature tree (or explicitly
   reviewed fixes), not by reverting unrelated later changes. Restore dashboard
   presentation tests and explicitly replace the recovery no-entrypoint invariant.
   Preserve storage/helper hashes, alias, package and signer; synchronize release
   identity across Phone/Wear/Core metadata as existing policy requires.
5. Require separate return PR/protected CI/security review and trusted signed
   artifact verification. Do not reuse an alpha40 tag or attempt lower-code install.
6. After explicit device approval, install recovery as a same-signer, higher-code
   in-place update only. Recheck package UID/install lineage, essential functions,
   history/settings/auth baseline. Do not wipe, uninstall or restore databases.
7. After separate return-install approval, update to the higher-code feature build.
   Export the public identity again: fingerprint must equal the pre-recovery and
   enrolled values. A new key is a FAILED preservation result, not a reason to
   silently replace enrollment. Recheck signed STOP and all retained-data baselines.
8. Guardian stays STOPPED throughout. Resume tests remain denied at Phase1 HTTP;
   cryptographic valid-resume behavior is tested in the isolated harness only.
   Never forward an activating request to the live broker for this rehearsal.

An unexpected storage/auth/key regression stops the rehearsal for diagnosis.
Do not “recover” by clearing data, downgrading, rotating keys or repeating unknown
operations. Software source compatibility is not a backup or physical proof.

## Controlled observer gateway deployment and rollback — not executed

Use alpha40 approved gateway source d4013a0, not whichever candidate happens to
be newest when approval arrives. Existing installer requires clean exact canonical
HEAD, so deploy before a later recovery merge OR re-review the then-current exact
canonical source and its unchanged gateway files. Never bypass that source gate.

Predeployment capture, after authorization and with no active executing tools:

- Exact runtime symlink target, source SHA, unit/drop-ins and supported environment
  file in a private operator-owned recovery directory. Do not print secret values.
- Token/API credential unchanged checks in-process; preserve files in place.
- Developer journal schema and content-free thread/tool-state counts; consistent
  SQLite backup if needed, never raw copying an actively written journal. Backup
  is protected evidence, not permission to restore old user data during rollback.
- Service status, authenticated readiness and unauthenticated denial, Core health,
  Tailscale Serve configuration identity, Guardian state/generation/trust.
- Record installed root Guardian/broker identities; do not alter them.

Install only gateway via the established installer. It also builds the fixed
Developer tool image and restarts that user service; this disruption needs explicit
approval and a safe turn boundary. Do not deploy Core or edit Tailscale. First test
with observer OFF: valid auth gets 503 on observer routes, bad/missing auth gets
401, manual Developer history/readiness works and API backend remains healthy.

Temporary approved opt-in uses `~/.config/jarvis/developer.env` and:

```text
JARVIS_AUTONOMY_OBSERVER_ENABLED=true
JARVIS_AUTONOMY_OBSERVER_STATE=/home/aaron/.local/share/jarvis-developer/observer-phase1/roadmap.sqlite3
```

The explicit directory is under the existing unit's ReadWritePaths. Default
`.local/share/jarvis-observer` is outside it; do not widen service permissions.
Journal must be separate from Developer state and outside every source worktree,
private 0700 directory / 0600 DB/lock. Startup failures are isolated from manual
Developer. Observer refresh is model-free/coalesced, with six-hour scheduled review;
it has no coding/worktree/Git/deploy executor. Restore OFF after temporary testing
unless expressly approved otherwise; leave the evidence journal intact.

Verify authenticated snapshot/observe, missing/bad bearer denial, body bounds,
untrusted/unknown Guardian handling, signed STOP and RESUME denial before IPC.
After controlled restart verify same roadmap/dedup state, zero engineering calls
and manual Developer compatibility. Never put auth in argv/logs/PR/Android output.

Rollback trigger: wrong source, readiness/auth/history regression, failed manual
Developer check or observer startup/storage failure. Disable opt-in; restore exact
captured previous runtime symlink/unit/config using an atomic symlink replacement,
daemon-reload and restart ONLY Developer at a safe boundary. Previous runtime
429cee8 exists. State/API/backend modules are unchanged across 429cee8→d4013a0.
Keep compatible live Developer history and observer journal; NO database rollback,
credential reset, integration reconnect or Guardian/broker reinstall. Verify old
source/health/auth/history/Tailscale and trusted STOPPED. If compatibility/outcome
is uncertain, stop and reconcile rather than blindly restoring data.

## Required approvals / certification decision

| Action | Authorization / remaining gate |
| --- | --- |
| Recovery local work, push, PR, checks | Authorized in this continuation; no merge |
| Recovery canonical merge | Explicit exact-head approval after green checks/review |
| Non-promoting signing mechanism | Separate reviewed policy/workflow authorization; not implemented |
| Production recovery/return signing | Explicit approval; existing protected signer only |
| Public release / alpha feed | Separate approval; NOT implied by artifact preparation |
| Gateway installation + temporary observer | Exact source/scope/window and rollback approval |
| Alpha40 phone installation | Not needed: already verified installed; no reinstall authorized |
| Public-key enrollment | Separate Aaron local privileged ceremony, real device bundle |
| Signed live STOP / invalid-envelope tests | Explicit controlled test approval, STOPPED throughout |
| Recovery and feature-return installation | Separately approved exact signed APKs, higher codes, retained-data baseline |
| Watch update / Core / Guardian / broker deploy | Not authorized or needed for this step |
| Phase 2 / autonomous execution | Prohibited |

**PHASE 1 BLOCKED** pending live observer/dashboard, essential functions, real
enrollment/signed controls and independently verified data-preserving recovery.
Successful PR CI certifies source checks only, not those physical requirements.
