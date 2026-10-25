# Phase 1 release-candidate certification matrix

## Recovery artifact-only signing continuation — 2026-10-25

This section supersedes the historical release blockers below, not the physical
acceptance requirements. Alpha40 is released at canonical
`d4013a0137a5b88e3e401627084b35d58c8c813a`; no runtime deployment is inferred from
that Git state. See [artifact-only signing review](ANDROID_ARTIFACT_ONLY_SIGNING.md)
for the exact proposed permission boundary and approval sequence.

| Requirement | Status | Current evidence / remaining gate |
| --- | --- | --- |
| Alpha40 release | PASS | PR97 merged; v19.0.0-alpha40/190420 Phone/Wear production signatures and hashes independently verified; PR97 comment 6393482224 |
| Recovery source | PASS (review/local) | PR98 head ac3f3f1e2ac442fa4ded96d1229e51481b4b6636; alpha41/190430 restores actual released alpha39 Android behavior, preserves storage/aliases; not merged |
| Recovery protected checks | PASS (authoritative CI) | All four required contexts on exact PR98 head; runs 40182269133 and 40182269141 |
| Recovery separate security gate | PASS | CodeQL analysis 2021791832, matching merge tree 4344144ef36b38f14d48a4fea8895de298ad92c8, zero findings, no open PR alerts; not a claim that all historic Core findings are resolved |
| Artifact-only signer safety | PASS (local harness) | 71 new tests: source/check/approval gates, stale-source rejection, bounded paths, signer/package checks, key cleanup, read-only workflow structure; 88 combined signer/manifest tests |
| Full Python regression with signer | PASS (local) | 2,164 passed, 3 skipped, 143 subtests; bridge/tests tools/tests developer_gateway/tests; not a signing run |
| New signer authoritative CI | BLOCKED pending exact-head PR | Separate infrastructure PR required; local success does not authorize merge or signing |
| Human signing approval environment | BLOCKED | No repository environments configured; jarvis-artifact-signing must be explicitly approved/configured with Aaron-only required reviewer; code fails closed meanwhile |
| Signed recovery / higher-code return | BLOCKED | Neither artifact exists; canonical-only trust retained; approve source sequence before recovery merge, then exact-head CI and separate signing approval for each; no tags/feed changes |
| Public OTA / installed host infrastructure | PASS (unchanged by this work) | Existing publishing workflow untouched; no signing dispatch, tag, release, host install, enrollment or activation performed |
| Guardian | PASS (live read-only) | STOPPED/trusted=true; no control operation performed |
| Samsung installed alpha40 | PASS (package bytes) | Same production package/signature; version190420; fresh device APK hash d30d92894ca2266676468aa2c589ac55dd0388e5e314c7e3634aadb956f17482; update history alone does not prove who installed it |
| Current Samsung connectivity | BLOCKED | Latest ADB/mDNS lists empty after earlier authorized SM_G996B observation; reconnect only this phone, no silent pairing |
| Home/dashboard rendering | NOT TESTED (full acceptance) | Earlier physical Home/Development launches observed; dashboard truthfully reports observer unavailable, not successful live observation |
| Observer deployment / live dashboard | BLOCKED | Default remains OFF; separate deployment and temporary observe-only approval needed; private journal under existing Developer writable data directory |
| Real operator enrollment / signed STOP | BLOCKED | No real key enrolled or signed STOP accepted; public-key-only local ceremony and approval still required |
| RESUME boundary | PASS (software only) | Phase1 relay denies RESUME before IPC; no activation allowed for physical test |
| Retained chats/settings/auth/Keystore | NOT TESTED | Original install timestamp/UID retained but insufficient; require content-free operator baseline and explicit before/after checks |
| Physical recovery and feature return | NOT TESTED | Same signer, strictly increasing version codes, both verified artifacts in hand; no uninstall, clearing, downgrade or database restore |
| Phase1 overall | BLOCKED | CI alone is not physical certification; no Phase2 authorization |

All new source work is on the isolated signing-infrastructure branch. Canonical,
recovery, production runtimes, devices, credentials and Guardian state are not
modified. Current signing PR/check evidence belongs in its exact-head PR record;
do not reinterpret an earlier green head after any source change.

## Historical alpha40 candidate evidence

Date: 2026-10-24. Branch `jarvis/autonomous-development`, based on production
`429cee8ac2d8efdc5d7c99805699edae78e9a85d`. The immutable candidate commit is the
PR head containing this document; subsequent CI results must identify that exact
head. Local validation is not authoritative CI or physical acceptance.

Scope: Android dashboard + optional observe-only API + reviewed host tooling.
Candidate alpha40/190420; public release remains alpha39. Guardian STOPPED;
observer disabled by default; no autonomous coding/merge/deployment executor.

| Requirement | Status | Reproducible evidence / limitation |
| --- | --- | --- |
| Core, tools, gateway regression | PASS (local) | 2,093 passed, 3 skipped, 143 subtests; command below |
| Phone unit tests | PASS (local) | 276 tests, zero failures; Gradle XML reports |
| Wear / shared protocol tests | PASS (local) | 1 / 30 tests; same Gradle gate |
| Phone/Wear lint and debug builds | PASS (local) | Full gate BUILD SUCCESSFUL; debug is not production signing |
| Ruff / format / active-source mypy | PASS (local) | 252 formatted files; 111 typed files; commands below |
| Broker/enrollment focused typing | PASS (local) | Explicit namespace-package mypy invocation below |
| Security / dependency audit | PASS (local) | High-severity Bandit; pip-audit no known vulnerabilities |
| Workflow / source secret scan | PASS (local) | actionlint 1.7.12; Gitleaks 8.30.1 source-only snapshot, no leaks |
| Product baseline / syntax | PASS (local) | Baseline --skip-ref, compileall, shell syntax |
| Stage B and Assist | PASS (local) | 13/13, WER 0.013889, semantic/tool/argument/critical-token 100%; streaming 6, closure 6; Assist package gates passed |
| Systemd definitions | PASS (local static) | Six units verify; unrelated host xfs CPUAccounting warning is nonfunctional; no installed unit changes |
| Guardian trust / stopped state | PASS (live read-only) | `jarvis-autonomy status`: STOPPED, trusted=true, generation 0c8b01fb-1347-4d2b-83b7-9a9c1e80ef89 |
| STOPPED admission, IPC, replay, audit, recovery | PASS (root-free tests) | tools/tests/test_autonomy*; prior installed admission-module probe; not claimed as live worker UID/socket execution |
| Observer auth / disabled default / failure isolation | PASS (local) | Gateway tests: unauthorized 401, disabled authenticated 503; startup exception cannot break manual Developer |
| Read-only live observation / restart | PASS (sidecar) | tools/certify_autonomy_observer.py; /tmp/jarvis-observer-http-phase1-tz8udb84; two process cycles, same durable journal |
| Honest planning / dedup | PASS (local + sidecar) | Sidecar collected 3 observations, 0 goals, honest idle; system-health endpoint timeout stays unknown, not fabricated failure |
| Zero engineering during observer probe | PASS | Developer threads=0, tools=0, canonical unchanged; Governor has no engineering entrypoint |
| Research provenance / injection boundary | PASS (local) | ResearchEngine adapter tests retain bounded HTTPS provenance as hypotheses, no authority |
| Live purposeful research | NOT TESTED | No justified research question in current observations; no configured live research provider; no model calls claimed |
| Grounded dashboard capabilities / idle / failures | PASS (local) | Presentation and Governor tests; unknown integrations explicitly unverified; API requires configured AND healthy |
| Phase 1 RESUME denial | PASS (local) | Signed RESUME receives 403 before IPC; no UI activation action; host CLI separate |
| Signed STOP / protocol / enrollment | PASS (harness) | Python/Java public vectors, HTTP relay + broker dispatcher, enrollment/revocation rehearsal; no real phone key enrolled |
| Protected PR checks | BLOCKED pending corrected-head CI | PR #97; all four required jobs passed 450e1d5, but the separate CodeQL alert gate flagged permissive defaults in the new test interceptors; defaults tightened, fresh checks required |
| Human release/security approval | BLOCKED | Must precede canonical merge/signing/release; this matrix is not approval |
| Production signed candidate | BLOCKED | Canonical-tag CI only; no alpha40 signed artifact/hash yet |
| Signing continuity | PASS (historical inspection only) | Installed cert 009fd523f27cf94eb98917e17670804897e6378e5eccf1ce3ead680721691aac; debug differs, correctly not installed |
| Current Samsung connection | BLOCKED | Latest adb devices -l empty; previous authorized SM_G996B remains historical evidence |
| Data-preserving physical update / essential functions | NOT TESTED | No installation/uninstall/pm clear; explicit install approval and compatible signed APK required |
| Physical dashboard / STOPPED / capabilities | NOT TESTED | No feature build on Samsung; observer production wiring not deployed |
| Real Keystore key / enrollment / signed STOP | BLOCKED | Requires compatible phone build, public bundle and operator enrollment; fixtures are not real enrollment |
| Higher-code same-key recovery build | BLOCKED | Procedure reviewed; signed recovery build and retained-data rehearsal not performed |
| Privileged sandbox/Core release/rollback live execution | NOT TESTED | Not authorized in Phase 1; root-free tests only, installed broker remains STOPPED |

## Reproduction

From this worktree, with the repository dependencies installed:

```sh
PYTHONPATH=bridge:. python -m pytest -q bridge/tests tools/tests developer_gateway/tests
python -m ruff check bridge/app bridge/tests developer_gateway tools
python -m ruff format --check bridge/app bridge/tests developer_gateway tools
python -m mypy bridge/app developer_gateway tools/generate_ota_manifest.py tools/generate_product_release_manifest.py tools/verify_product_baseline.py
python -m mypy --explicit-package-bases --follow-imports=silent tools/autonomy_broker.py tools/enroll_autonomy_operator.py
python -m bandit -q -r bridge/app developer_gateway -lll
python -m bandit -q -lll tools/autonomy*.py tools/enroll_autonomy_operator.py
python -m pip_audit -r bridge/requirements.txt -r requirements-improver.txt -r developer_gateway/requirements.txt --progress-spinner off
python tools/verify_product_baseline.py --skip-ref
PYTHONPATH=. python tools/certify_autonomy_observer.py
```

Local Python 3.14 environment: `/home/aaron/.venvs/jarvis-phase2`; CI uses 3.12,
so authoritative CI is still required. The Starlette/httpx deprecation warning is
not a failed test. Focused tool typing requires `--explicit-package-bases` because
tools is a namespace package; a combined filename invocation without it duplicates
module names, not a source defect.

Android, from `android/jarvis-voice-client`, JDK17/SDK36:

```sh
./gradlew --offline --no-daemon :wearprotocol:test :app:testDebugUnitTest :app:lintDebug :app:assembleDebug :wear:testDebugUnitTest :wear:lintDebug :wear:assembleDebug
```

Secret scanning uses a disposable snapshot of tracked and intended new source,
not private runtime/credentials, build downloads or unrelated worktrees. The CI
scanner scans its fresh checkout. No scanner exclusion or security gate changed.

## Safety review and remaining architectural scope

PR: https://github.com/aaronhall105/jarvis-ai/pull/97. Initial candidate
`e89b6035f66e2bbe3c8dd744a3d7a3eb0095e388` received three CodeQL
`py/overly-permissive-file` alerts. Installers/sandbox snapshots now create files
privately (0600), write/flush complete payloads, then apply the required final
public helper/source read modes. Seven regressions inspect permissions at creation
and publication; **98 focused tests passed** including installer/Guardian/snapshot
rehearsals. No scanner suppression. Installed root payloads were NOT changed;
any later host upgrade needs separate reviewed handling of its pinned manifest.
On `450e1d5e314505ca0ffdfeef70259eadd0f172cd`, all four required jobs passed and
the original three alerts were resolved. The separate CodeQL alert gate caught
two permissive default arguments in the new test `os.open` interceptors; those
test defaults are now 0600 as well. Do not equate a successful analysis job with
a clean security-alert gate. Final CI must cover the corrected test source.

- Product observer is off unless the setting is exactly `true`. It uses a separate
  private SQLite journal outside source, fixed local health URLs, bounded reads,
  no redirects/proxy and typed evidence only. Manual Developer auth is unchanged.
- The read-only evidence implementation is intentionally limited to health; it is
  not yet an AI product manager, complete capability inventory or engineering loop.
  No false production improvements or capabilities are reported. RECENT remains
  empty until actual production evidence exists.
- STOP relay requires authenticated Developer access AND independently enrolled
  operator signatures at the root broker. No enrollment API/shared resume secret.
  Controls are not retried on HTTP failure; authority is refreshed from host state.
- Root-free security tests cover kernel-peer identity checks, schema/path/hardlink/
  symlink rejection, fixed sandbox profiles, STOP cancellation, protected checks,
  exact-head release provenance, ledger replay and uncertain effects. Installed
  root files are unchanged by this feature commit or its future source merge.
- Broker supports fixed Python/security sandbox and conservative Core candidate/
  install/rollback only. Android/Developer deployment and arbitrary services/images
  are rejected, not partially implemented. Release review identities are not
  provisioned; no claim of live autonomous release readiness is made.
- Software rollback never restores a database snapshot. Core data-compatibility
  tests reject persistence/dependency/schema changes without a proven compatible
  path; exact previous software, health baseline and outcome are recorded. Unknown
  effects require reconciliation. This is tested architecture, not live deployment.
- Enrollment is an explicit privileged local ceremony; incomplete transactions
  fail closed and require root-reviewed reconciliation, not silent reinstall.
- Android rollback must be a same-production-signature **higher-versionCode**
  known-good recovery release; never uninstall, clear data, replace signing keys
  or promise an in-place downgrade. Physical data/Keystore preservation is unproven
  until the authorized device rehearsal actually runs.

## Decision

**PHASE 1 BLOCKED** for final physical certification. Local software gates passed;
protected CI, human approval, signed artifacts, observe-only runtime wiring, device
acceptance, real public-key enrollment and recovery proof remain distinct gates.
Follow [the release sequence](autonomy-signing-review.md). Do not activate Phase 2.
