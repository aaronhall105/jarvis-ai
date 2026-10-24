# Phase 1 release-candidate certification matrix

Date: 2026-10-24. Branch `jarvis/autonomous-development`, based on production
`429cee8ac2d8efdc5d7c99805699edae78e9a85d`. The immutable candidate commit is the
PR head containing this document; subsequent CI results must identify that exact
head. Local validation is not authoritative CI or physical acceptance.

Scope: Android dashboard + optional observe-only API + reviewed host tooling.
Candidate alpha40/190420; public release remains alpha39. Guardian STOPPED;
observer disabled by default; no autonomous coding/merge/deployment executor.

| Requirement | Status | Reproducible evidence / limitation |
| --- | --- | --- |
| Core, tools, gateway regression | PASS (local) | 2,086 passed, 3 skipped, 143 subtests; command below |
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
| Protected PR checks | BLOCKED pending corrected-head CI | PR #97; initial e89b603 passed core-tests/quality-security but CodeQL reported 3 permission-initialization alerts; source corrected with regressions, all exact-head checks must rerun |
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
