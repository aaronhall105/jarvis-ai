# Phase 1 physical preparation — 2026-10-25

This is the preparation snapshot preceding recovery PR authorization. See
[the recovery review](autonomy-recovery-review.md) for subsequent implementation,
full validation, PR/CI scope, non-promoting signing proposal and approval matrix.
In particular, the final recovery retains the unused operator-contract helper and
shared security fixtures; it does not expose the dashboard or operator controls.

## Scope and current decision

Preparation only. No phone/watch installation, deployment, enrollment, activation,
push, merge, release tag or OTA mutation was performed in this continuation.
Guardian remains trusted STOPPED. Phase 2 is prohibited. This record supersedes
older pending-alpha40 / disconnected-phone statements in the historical reviews.

Release evidence: https://github.com/aaronhall105/jarvis-ai/pull/97#issuecomment-6393482224

## Exact source / artifact identity

- Remote canonical and alpha40 peeled tag: `d4013a0137a5b88e3e401627084b35d58c8c813a`.
- Approved PR head / unchanged clean feature worktree: `4655806599496d32610af21da5c535f9d12ec5a8`.
- These source trees are identical. Existing protected CI and release validation
  are recorded in the linked evidence; they were not needlessly rerun.
- Fresh exact-commit check-run inspection still reports all four protected jobs
  SUCCESS, including the latest scheduled CI run 40176486382. Recovery-branch
  checks below are local only; no remote recovery CI was triggered.
- Canonical local worktree remains clean at `429cee8ac2d8efdc5d7c99805699edae78e9a85d`.
- Core remains healthy at `8b8d7b08160e3ea36f9d944e2ab2528b37dddc0a`.
- Developer remains healthy at `429cee8ac2d8efdc5d7c99805699edae78e9a85d`,
  backend API, configured and healthy. No gateway deployment occurred.
- A fresh detailed Core `/api/system/status` request timed out at eight seconds.
  Per-integration health/speculation evidence was unavailable in that request;
  previous evidence is not represented as a fresh check. No integration or
  speculation configuration changed. Do not label the timeout a proved outage.
- Guardian generation remains `0c8b01fb-1347-4d2b-83b7-9a9c1e80ef89`, STOPPED/trusted.
- Existing Core CodeQL findings remain separate from PR97's passing security gate:
  the release record documents 24 pre-existing findings, not zero global findings.

Both published APKs use package `com.aaron.jarvisvoice`, alpha40 / 190420,
certificate SHA256 `009fd523f27cf94eb98917e17670804897e6378e5eccf1ce3ead680721691aac`.

| Artifact | SHA256 |
| --- | --- |
| Phone | `d30d92894ca2266676468aa2c589ac55dd0388e5e314c7e3634aadb956f17482` |
| Wear | `79177ae2eaab91f7a29ee375fb2d70c87ae9b8ccf0e2fbbb5434b9d4fa16d4ee` |

Fresh GitHub asset metadata agrees with the prior independently verified artifacts.
No release asset/feed changed. The existing OTA workers may notify/download but
do not install; an installer intent requires user action. Wear has no automatic
GitHub OTA installation path.

## Important new physical evidence

Authorized device: `adb-RFCR10C1K2H-qFUPIw._adb-tls-connect._tcp`, Samsung SM_G996B,
product t2sxeea / device t2s. mDNS currently advertises `192.168.1.149:36845`.
Use a fresh `adb devices -l` before further work; these addresses can change.

Contrary to the earlier handoff, the phone ALREADY has alpha40 / 190420.
Package manager records last update `2026-10-25 07:49:27`, installer
`com.google.android.packageinstaller`; this does not establish who initiated it.
The agent performed no installation. A fresh read-only pull of the installed base
APK has the exact published Phone SHA above; SDK36 apksigner verifies the same
production signer and aapt verifies the package/code/name.

Public package evidence: UID 10392, first install `2026-08-30 00:22:22`, user 0
installed/enabled/not suspended, data directory `/data/user/0/com.aaron.jarvisvoice`.
These facts support continuity but do NOT prove retained conversations, settings,
authentication or Keystore keys. No private preferences/database/credential was read.
An all-user package query denied Samsung's separate user 150; only user 0 is in
scope. Future package queries should explicitly specify `--user 0`.

Read-only UI navigation of the existing installation succeeded:

- Home cold launch: Status ok, TotalTime 518ms; Jarvis UI nodes rendered.
- Jarvis Development entry opens the real dashboard.
- Dashboard shows NEEDS ATTENTION and “Development observation is not available
  yet. Jarvis is still running normally.” This is expected with undeployed API.
- No fake STOPPED/current-goal/capability/completed claims are shown without data.
- App process remained present. No claim of comprehensive crash-free operation.
- No operator-key setup/copy/enrollment/control button was invoked.
- User subsequently foregrounded another application; no further UI interruption.

Physical plans/capabilities/technical-details, chat/voice/Home/manual Developer
operations and retained private state are still NOT TESTED. A genuine pre-alpha40
baseline cannot now be reconstructed retroactively. The next recovery rehearsal
must use a new, operator-confirmed alpha40 baseline without exporting private data.

Installed APK copy: `/tmp/jarvis-phase1-physical-prep.9iFaYu/installed-samsung.apk`.
It contains application code only. No screenshots, transcripts or private state
were persisted. No uninstall, pm clear, downgrade, key rotation or data restore.

## Observer deployment preparation — requires separate approval

The signed phone alone cannot populate the dashboard. The approved source adds
optional routes to the existing Developer gateway, not a second engineering agent.
Current deployed gateway has no observer routes; its configuration has backend=api
and no observer opt-in. Code defaults OFF unless the enable flag is exactly true.

Proposed approval scope:

1. Recheck remote canonical still equals d4013a0 and local canonical is clean.
   Fast-forward that worktree to the exact approved commit only after approval.
2. Check for active manual Developer turns; do not interrupt an executing tool.
   Record current runtime symlink/unit, non-secret credential fingerprints locally,
   history counts and service health. Never display credential hashes or values.
3. Run the existing `bash developer_gateway/install.sh` from canonical production.
   It packages the observer modules, constructs a versioned gateway runtime and
   its fixed tool image, then restarts ONLY the Developer user service. No Core,
   Guardian, broker, Tailscale, integration or Android deployment is included.
4. First validate with observer OFF: manual Developer authentication/history and
   API health unaffected; unauthenticated observer requests 401, authenticated
   requests 503. Preserve existing Developer token, API credential and backend.
5. For an expressly approved temporary observe-only test, set the supported
   config in `/home/aaron/.config/jarvis/developer.env`, retaining other entries:

   ```text
   JARVIS_AUTONOMY_OBSERVER_ENABLED=true
   JARVIS_AUTONOMY_OBSERVER_STATE=/home/aaron/.local/share/jarvis-developer/observer-phase1/roadmap.sqlite3
   ```

   The explicit separate journal is necessary: the source's default path under
   `.local/share/jarvis-observer` is outside the current unit's ReadWritePaths.
   Use the already permitted Developer runtime directory; do not widen sandbox
   permissions. This new SQLite file must not be the manual Developer ledger.
   Runtime creates private directory/file modes (0700/0600). Restart Developer
   only at a safe turn boundary. Guardian stays STOPPED.
6. Authenticate in-process using the existing token file; never place its contents
   in argv, shell trace, output or screenshots. Test missing/bad bearer=401,
   valid snapshot/observe=200, unsigned control denied, resume=403 before IPC,
   oversized body=413, correct STOPPED generation and truthful capabilities.
   Verify restart/dedup persistence and zero Developer tool/job execution.
7. Test phone rendering and established essential functions; disable the opt-in
   after the temporary test unless continuing it is expressly approved. Preserve
   the observer journal for evidence; default behavior remains OFF in source.

Gateway recovery: the previous versioned runtime `releases/429cee8...` is present.
Developer state/backends/API tools and service template are unchanged by PR97.
Record the complete prior symlink and unit before installation, not guessed paths.
On failure disable observer, restore the exact previous runtime/unit and restart
only Developer; retain Developer history, credentials and observer journal. No
database rollback. This is a prepared recovery procedure, not an executed test.

## Operator enrollment / signed STOP preparation

Follow `docs/autonomy-operator-enrollment.md` from the approved feature source.
After separate authorization, the real phone's Set up operator controls creates
or reuses Android Keystore alias `jarvis.autonomy.operator.v1`; never replace an
existing valid key. Copy only the public bundle; compare its fingerprint on-phone.

Operator-only command, NOT executed and not needed until the real bundle exists:

```sh
sudo bash /home/aaron/jarvis-autonomous-development-worktree/tools/enroll_autonomy_operator.sh enroll
```

Aaron enters the public bundle/fingerprint locally; any password stays in the
terminal. This enrolls one public identity, updates protected public-policy hashes
and audit, and preserves STOPPED. No private key is exported and no reinstall is
needed. Existing different enrollment requires explicit replacement/revocation.

After enrollment verify identity equality; send signed STOP from Samsung, require
authoritative response and refreshed STOPPED/trusted state. Repeat completed request
returns the durable receipt without another effect; altered reuse/expiry/stale
generation/unknown key must be denied. Test invalid envelopes against the gateway,
never turn these tests into a live resume. Existing isolated signature/relay tests
prove valid signed RESUME is denied by Phase1 HTTP before IPC. Production UI has
no RESUME button; do not add a hidden activation path for physical testing.

## Isolated recovery candidate / safety review

Local branch `jarvis/phase1-alpha41-recovery`, worktree
`/home/aaron/jarvis-phase1-alpha41-recovery-worktree`, base d4013a0. No commit or
remote publication. Alpha41/190430 was unreserved in the fresh remote tag check;
recheck before any future release. The original feature worktree remains clean.

Applied the earlier proposed patch (SHA256
`26249f2dda6bbe354f4bb20f02208f5d992026a61c411f3378870cd5ea6b9fd4`)
only here after `git apply --check` against merged alpha40. Source edits used the
patch editor. Corrected its stale MainActivity baseline digest to the exact alpha39
recorded digest `9d77fc5de3f66a8869816ac514911e6a815de90f6560c4cd5d51ce1623e91c51`;
baseline checks remain enforced, not suppressed. Release archive updated truthfully.

All 310 tracked Android baseline files match signed alpha39 source
`42cfa630ce4e81f14f73de457dc3c822c53958a8`, except the exact release-name/code
substitutions in five version/test files. Alpha40-only Android files are removed
from this recovery candidate, not from production. Package and signer policy are
unchanged. SecureStore, ChatHistoryStore and TurnRecoveryJournal are byte-identical
across alpha39, alpha40 and this candidate. No schema migration or data restore.

Tradeoff: exact alpha39 recovery removes the Development dashboard AND the PR96
Android response-rendering changes. It intentionally restores the actual prior
released client, not all improvements in the later server source. Gateway remains
API-backed. Host tooling stays dormant; Core changes are version metadata only.

New local validation (not CI, not physical certification):

- 19 release-identity/product-baseline Python tests PASS; baseline CLI PASS.
- Phone 259, Wear 1, wearprotocol 30 unit tests PASS; no failures/errors/skips.
- Phone/Wear debug lint PASS. Gradle used offline cached dependencies/JDK17/SDK36.
- Changed Python Ruff/format PASS; diff whitespace PASS.
- Source equality and unchanged storage-source comparisons PASS.
- Initial focused test collection lacked the usual isolated data dir/mock API
  value; rerun used temporary data and a deliberately invalid test placeholder,
  never a real credential. No model call. No product code fix was needed.
- No signed or installable recovery artifact produced. Wake assets/release build,
  full repository gates and authoritative protected CI remain future requirements.

Reproduce focused checks from this worktree with the existing test venv, temporary
`JARVIS_DATA_DIR`, `JARVIS_PROACTIVE_DB_PATH`, `JARVIS_VISION_DB_PATH` and dummy
OPENAI_API_KEY; run pytest on `bridge/tests/test_final_polish_alpha13.py`,
`bridge/tests/test_release_alpha15.py`, `tools/tests/test_verify_product_baseline.py`.
Run `tools/verify_product_baseline.py --skip-ref`. Android command:

```sh
JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 ANDROID_HOME=/home/aaron/Android/Sdk android/jarvis-voice-client/gradlew -p android/jarvis-voice-client --offline --no-daemon :wearprotocol:test :app:testDebugUnitTest :app:lintDebug :wear:testDebugUnitTest :wear:lintDebug
```

## Signed recovery policy / approvals still required

The only current trusted signer is `.github/workflows/android-ota-release.yml`.
It requires a tag at exact canonical HEAD, runs gates, signs in CI, publishes
Phone/Wear and promotes the alpha OTA feed. No private feature-artifact mode or
approved local signer exists. No signing secrets were accessed. A verified signed
recovery artifact cannot be produced under the current no-publication restriction.

Recommended sequence, with separate explicit approvals at each boundary:

1. Authorize publishing this recovery branch as a reviewed PR and running protected
   CI (not merge/tag/release). Finish exact-head checks and security review.
2. Only then approve its exact canonical merge and alpha41 signed public release /
   feed promotion, OR separately approve designing a reviewed staging-only signing
   process. Do not bypass current controls or silently add a signer.
3. Independently verify recovery source, package, sole certificate, increasing
   code, manifest, sizes and hashes before any recovery installation approval.
4. Separately approve the observe-only gateway procedure above. No Core/host deploy.
5. Separately approve public-key enrollment and signed STOP/denial tests; no resume.
6. Separately approve an in-place recovery rehearsal: record non-sensitive alpha40
   history/settings/auth/key fingerprint baseline, then same-key higher-code update
   only. Check each baseline after recovery. No uninstall, clearing, downgrade,
   private-key export or database restoration. Stop on any mismatch.
7. Recovery removes the dashboard/public-key UI. Full round-trip key/dashboard
   verification needs another reviewed, same-key HIGHER-code feature-return release
   (number reserved only when approved), then separately approved installation.
   Reinstalling lower-code alpha40 is not an approved return path.

The original aim of having signed recovery BEFORE alpha40 installation can no
longer be proved: alpha40 was already on the phone when this session began its
device inspection. Do not misrepresent the timing. No watch update is included.

**PHASE 1 BLOCKED:** observer deployment, real enrolled key/signed STOP,
essential-function/data-retention acceptance and signed recovery rehearsal remain.
No Phase 2 command or autonomy activation is authorized by this preparation.
