# Developer recovery — controlled Alpha40 integration

Date: 2026-10-26. Original integration status: **READY FOR COMMIT/PR PREPARATION AUTHORIZATION**.
No commit, push, PR mutation, deployment, signing, APK installation or autonomy
activation was performed. This is local integration evidence, not protected CI or
production certification.

## Authorized GitHub review follow-up

The operator subsequently authorized one repair-branch commit/push and one PR,
but no merge, deployment, release, device update or autonomy activation. The
sections below preserve the original local integration evidence, not a claim
that GitHub certification has already completed.

Pre-push canonical verification still returned
`d4013a0137a5b88e3e401627084b35d58c8c813a`. No matching remote repair branch or PR
existed. All three workflow files are unchanged: the PR runs CI/CodeQL, while
production Android signing/publication requires a `v*` tag. The push is restricted
to the repair branch with tag following disabled. Repository webhooks and
additional rulesets are absent; the four protected checks remain enforced.

Final focused rerun: **151 passed**, one existing Starlette/httpx deprecation
warning. Gitleaks8.30.1 scanned the complete intended source snapshot (including
new tests/reports/probes),8.80MB, with **zero findings**. Guardian remains
STOPPED/trusted. Python3.12 and CodeQL results must be recorded from the exact
pushed head in the PR; local Python3.14 results do not substitute for them.

Deployment/rollback opt-in tests remain explicitly **SKIPPED** (three tests).
Physical Android testing is **NOT TESTED** for this repair. A future approved
software rollback must preserve task history, the completed-call ledger and
credentials; restoring an older database could lose exact-once evidence and is
not an approved rollback procedure. No live deployment/rollback proof is claimed.

## Verified source and preservation

- Canonical GitHub ref and local commit object independently agree:
  `d4013a0137a5b88e3e401627084b35d58c8c813a` (PR97 squash, Alpha40).
- New worktree: `/home/aaron/jarvis-developer-recovery-integration-worktree`.
- New local branch: `jarvis/developer-recovery-integration`.
- HEAD remains the canonical SHA above; all integrated changes remain unstaged.
- Original: `/home/aaron/.local/share/jarvis-developer/agent/worktrees/api_166a0e7b58294991b78a5a61578d8be8`,
  branch `jarvis/developer-166a0e7b58294991b78a5a61578d8be8`, base
  `429cee8ac2d8efdc5d7c99805699edae78e9a85d`.
- Original binary diff SHA256 before/after:
  `b4d45a7fe53ebab759d59442620d46693327aa777ad0c5abea84b553d8930849`.
  Every modified/untracked original file's SHA256 also matched before/after.
- Canonical local worktree remains clean at its previous 429cee8 HEAD; it was not
  fast-forwarded. No original journal/database was opened for mutation.
- PR98 remains OPEN at `ac3f3f1e2ac442fa4ded96d1229e51481b4b6636`.
  PR99 remains OPEN at `a586125786fa7971138a9cbd1bb72981f059eb5d`.
  Their separate worktrees remain clean and untouched.

The complete historical repair report is preserved in
[developer-recovery-repair.md](developer-recovery-repair.md). Its earlier
authorization/blocker statements are historical; this document records the newly
authorized integration. Original deleted-thread diagnostics remain unavailable.

## Narrow integration and review

Applied the reviewed source diff as hunks, not a whole-tree replacement. No merge
conflicts occurred: the repaired gateway files and Android Developer rendering
files were unchanged between the original base and Alpha40. Alpha40 introduced
observer wiring in other files, which remains intact.

Changed tracked files:

- `developer_gateway/api_backend.py`: recover reads, preserve completed effects,
  bounded stream retries, normalized errors/activity/history, robust completion,
  journal/client lifecycle.
- `developer_gateway/state.py`: explicitly read-only reconciliation and normalized
  legacy activity; uncertain writes remain fail-closed.
- `developer_gateway/backends.py`: discard closed API owner on restart.
- `developer_gateway/policy.py`: preserve non-secret source references while
  redacting literals/known secrets and structured string values.
- `developer_gateway/tools.py`: no-branch PR inspection becomes a completed
  negative result; reject newly introduced redaction placeholders in source edits.
- `developer_gateway/tests/test_api_backend.py`: explicit failed-item/no-false-final
  assertions, not weakened success criteria.
- Android `DeveloperActivityPolicy.java`, `MainActivity.java`,
  `DeveloperActivityPolicyTest.java`: display restored activity, safe failure
  messages and resumable status using the existing protocol/parser.
- `docs/JARVIS_PRODUCT_BASELINE.json`: only recomputed MainActivity digest changed
  to `31f8d86c3dc161b604c2e594472c228911e1a1bf2dd481145a02c6f5096099ea`.
  All Alpha40 baseline fields retained.

New files transferred:

- `developer_gateway/tests/conftest.py`
- `developer_gateway/tests/test_recovery.py`
- `developer_gateway/tests/test_certification_probes.py`
- `tools/certify_developer_recovery.py`
- `tools/certify_developer_read_recovery.py`
- `docs/developer-recovery-repair.md`
- This integration report.

One integration-only harness adaptation pins the optional Alpha40 observer OFF,
isolates its potential state path, and asserts unauthenticated401/authenticated503
responses plus absence of observer journal across application restarts. This does
not modify the production observer implementation or its configuration.

No dependency, schema DDL, release workflow, signing, OTA, package identity,
version, permission policy, Guardian/broker payload or production configuration
change. Existing task metadata receives only the reviewed recovery migration;
completed ledger entries are retained, not replayed. `verify`, writes and protected
Git/PR operations remain outside the recoverable-read allow-list. Existing
approval, ownership, identity, path and sandbox checks remain in place.

## Alpha40 preservation

Byte-for-byte unchanged against canonical HEAD:

- Gateway `app.py`, installer, observer, autonomy API/host/Governor modules.
- Android AutonomyActivity/Client/Control/Presentation, HomeActivity, manifests,
  version/build files, operator vectors and all other non-Developer features.
- GitHub workflows, systemd definitions, Guardian/broker implementation and policy.
- Alpha40 `19.0.0-alpha40` / `190420` metadata, signing and OTA paths.

The offline Android check reused the existing ignored Sherpa AAR dependency
(SHA256 `aa5505c0ec4f8bdaee5f214a64ba3012be64f2aecc022e82a64f33392b8dd245`).
No dependency installation, signing credential access, APK signing or device action
was needed. APK output count is zero. Unit tests compile the Phone/Wear source and
exercise Alpha40 dashboard/operator/protocol behavior; they do not prove physical
rendering on a Samsung or Wear device.

## Local test matrix

| Check | Result |
| --- | --- |
| Integrated Developer gateway suite | **151 passed**, including recovery, approvals, SDK errors, real restricted sandbox, socket/restart, Alpha40 observer and signed-control contracts |
| Full Python: bridge/tests tools/tests developer_gateway/tests | **2140 passed, 3 skipped, 143 subtests passed** |
| Guardian source/rehearsal suite after filesystem prerequisite correction | **63 passed**; all included in final full run |
| Phone unit tests | **277 passed**, no failures/errors/skips |
| Wear / shared Wear protocol | **1 / 30 passed**, no failures/errors/skips |
| Phone/Wear lintDebug and Java compilation | PASS, offline, Gradle9.4.1/JDK17/SDK36 |
| Ruff check / format | PASS,258 Python files |
| CI-equivalent mypy active-source command | PASS,114 files |
| Explicit-namespace probe typing | PASS,2 files |
| Compileall | PASS |
| High-severity Bandit on bridge/app + gateway + probes | PASS |
| pip-audit, existing three requirement files | No known vulnerabilities found |
| actionlint | PASS, existing workflows unchanged |
| Product baseline --skip-ref / git diff --check | PASS |
| Home Assistant streaming / closure / release integrity | 6 + 6 tests passed; integration1.8.0/config-entry2/11 required files verified |
| Current-tree secret scan | PASS; Gitleaks8.30.1,8.80MB,zero findings; snapshot `/tmp/jarvis-developer-integration-scan.UTG5F7`, including untracked tests/probes/reports, excluding runtime credentials/build outputs |
| New protected CI / CodeQL | NOT RUN: no commit/push/PR authorized |
| APK assembly/signing/install / physical UI | NOT RUN: intentionally outside authorization |

Full log: `/tmp/jarvis-developer-integration-validation.JogZwL/pytest-final.log`.
The three skipped tests explicitly opt into real Docker deployment/rollback;
they were not enabled. Tests ran on existing Python3.14; protected CI still must
validate Python3.12 before any integration approval beyond local review.
The existing Starlette/httpx deprecation warning remains; Java/Gradle report
existing deprecation warnings, not test or lint failures.

### Initial broad-run failure and resolution

Initial run: 2135 passed,5 failed,3 skipped. All five failures were Guardian
installer-source trust rehearsals: the host umask0002 made fresh checkout files
0664/0775, correctly rejected as group-writable. No recovery-code regression.
Removed group/other write permission only on these seven **new-worktree** files:
`tools/autonomy_guardian.py`, `tools/autonomy_guardian_install.py`,
`tools/autonomy_guardian_control.sh`, `tools/autonomy_guardian_cli.sh`,
`tools/install_autonomy_guardian.sh`, and both `systemd/autonomy/` service files.
Resulting modes0644/0755 match the intended source trust model. Contents and Git
executable bits stayed unchanged; no host-installed file was touched. Did not
relax the safety gate or modify tests. The focused63 and full2140 reruns passed.
Initial log retained as `pytest.log` beside the final log.

## Real-provider evidence on integrated Alpha40 wiring

Used the unchanged configured `gpt-5.4` model and dedicated Developer API key
mechanism. No credentials printed/copied. Opt-in read-only probe ran the repaired
FastAPI application with a fresh test token/private temporary journal, not the
installed gateway. It enforced a read-only tool allow-list independently of prompts.

Evidence: `/tmp/jarvis-developer-read-recovery-13msu_3c`.

- **PASS:** no-branch `pr_status` returned a completed actionable negative result.
- **PASS:** second `pr_status` reached GitHub for this integration branch; expected
  no-PR result, not an authentication failure. No PR was created or mutated.
- **PASS:** actual source search/read/repository inspection,5 completed calls.
- **PASS:** streamed tool progress, final responses, disconnect during tool start,
  reconnect, identical history/ledger after isolated application stop/start, and
  real provider continuation in a third turn.
- **PASS:** zero write tools, zero autonomous worktrees, unchanged source diff;
  observer disabled throughout with no observer database created.
- API usage: **18525 input + 507 output = 19032 tokens**,8 requests, complete usage
  metadata. Probe limits:12 requests per backend lifecycle,2048 output tokens per
  request,120-second task deadline, one transient retry. No production quota added.

The repaired runtime modules are byte-identical to the preserved self-repair WIP.
Its prior live supervised fixture proof remains applicable: one patch, unchanged
tests, successful restricted offline pytest, model final, restart persistence and
cached replay without a second effect. The integrated deterministic suite reran
those recovery/sandbox paths. No need to repeat a write-capable real-provider task
or reconstruct the historical deleted thread to claim this narrower integration.
This is in-process WebSocket/live-provider evidence, not physical-device/Tailscale
failure injection or a guarantee for arbitrary future long-running repairs.

## Remaining review boundary

No known integration defect remains. Ready for explicit authorization to commit
this narrowly scoped diff and prepare a single repair PR, **not** for deployment.
Before any future push, recheck canonical head and preserve this local evidence.
Protected CI/security review on the exact resulting commit is still mandatory.
Any later gateway rollout and Android release/installation need separate approval;
the production gateway still has the old implementation.

Guardian remained **STOPPED/trusted=true**, generation
`0c8b01fb-1347-4d2b-83b7-9a9c1e80ef89`. No production endpoints received state-changing
tests. No production service restart, container change, live journal mutation,
phone/watch operation, release activity, or autonomy/Phase2 activation occurred.

Official OpenAI documentation was consulted for the preserved completed-response
and error-event boundary: [Streaming API responses](https://developers.openai.com/api/docs/guides/streaming-responses).
It did not justify changing the model, tool authority or release controls.
