# Deleted Developer conversation recovery and uncertain-patch reconciliation

## Scope and preservation — 2026-10-28

Base: `e80cfea7301ac6c20e4eb7fac3cb61ff2252422f`, branch
`jarvis/unified-production`. Isolated implementation:
`/home/aaron/jarvis-deleted-developer-session-recovery-worktree`, branch
`jarvis/deleted-developer-session-recovery`.

Original session: `api_ee86384f47bd441ba232585ef7173a61`. Its worktree remains
`/home/aaron/.local/share/jarvis-developer/agent/worktrees/api_ee86384f47bd441ba232585ef7173a61`,
on `jarvis/developer-ee86384f47bd441ba232585ef7173a61`, based on the same commit.
Only `developer_gateway/app.py` is modified there. Nothing was reset, replayed,
committed or repaired in that worktree. The production SQLite journal was opened
with SQLite `mode=ro`, not through the mutating gateway State constructor.
No journal classification was changed.

## Single uncertain operation

- 25 completed calls; one uncertain `patch_file`; no-progress counter zero.
- Ledger row: 150; call: `call_GSJmo3f2tNrzCfKWqNF5JVou`.
- Signature: `616f9f64a016d30e4a8c875ca2fb69a6cf845c0fb14279974461bf9b33955d21`.
- Target: `developer_gateway/tests/test_gateway.py`.
- Recorded preimage **and current file** SHA256:
  `b6ce27c0408b525dd7103b96c511ad9d3b7b8b87ab721f83fd5f0d012689b075`.
- Calculated intended postimage SHA256:
  `e90aed73776e9137e1cf942ff777e2e03666090e8e03e70d634e5a9d38d05a29`.
- Intended replacement is absent; current bytes are exactly the preimage, not a
  partial replacement. File is regular, not a symlink, with mode 0600.
- Original has zero redaction placeholders; proposed replacement has four.

The current tool implementation rejects an increased redaction-placeholder count
with a plain `ValueError` **before** mkdir/write. It also raises a plain
`ValueError` for preimage mismatch. The backend handles explicit ToolPolicyError
as a completed negative result but records other exceptions from effectful tools
as uncertain. A disposable reproduction using the real runner/backend confirms
both preimage mismatch and new-placeholder rejection leave bytes unchanged while
recording an uncertain call: **2 passed**. This is a real classification defect.

The incident's exception/trace was not persisted (only a generic provider-category
failure and null call result). Therefore the payload necessarily fails the
redaction guard *if execution reaches it*, but an earlier filesystem exception
cannot retrospectively be excluded. Unchanged bytes alone do not prove the
historical exception or authorize automatic replay. Raw arguments and private
conversation content are intentionally omitted.

## Previously completed source work

Completed call row 149 wrote `developer_gateway/app.py`; its current SHA256 is
`5312b42a533ef690a776bcc88262f816ff6f85f487153fb0c58dea3432df821c`, exactly matching
the recorded successful replacement. That replacement is a truncated file:
the initial 259 lines (imports, application/authentication/lifecycle and socket
setup) were removed and parsing raises `IndentationError` on line 1.

The useful idea is request-local error handling and correlation for a missing
conversation. The fragment is not a usable gateway and was not copied wholesale.
Its concept was reconstructed on the complete canonical source. No Android source
or regression test had been successfully changed in the original worktree.

## Feature implementation

- State raises a typed `ThreadNotFound` only for absence of an API-thread record.
  Workspace mismatch, provider errors and text resembling a missing-thread error
  do not establish deletion. No schema migration or persistence change.
- Authenticated gateway request failures return their request ID and a safe fixed
  message. Confirmed absence adds `THREAD_NOT_FOUND` and the thread ID. The socket
  stays usable. Authentication, workspace/ownership checks, rate limits, approval
  ownership, interrupted-call handling and mutation retry policy are unchanged.
- Android correlates resume errors with outstanding request ID, requested thread,
  current selection and typed error code. Only that confirmed stale selection is
  cleared. Delayed responses cannot clear a newer selection or restore an old one.
- Incoming messages are processed on the UI handler with a connection-epoch check.
  Generic request errors no longer claim a transport reconnection is happening.
- Queued instructions do not silently move to another conversation after deletion;
  explicit user choice/new instruction is required. History cards, authentication,
  endpoint settings and other preferences are retained.
- MainActivity's existing product-baseline checksum was updated for the narrow UI
  edit. No version, package, signing, OTA, observer, autonomy or approval changes.

Compatibility: old clients still receive the existing error/message shape with
additive fields. New clients intentionally do not infer deletion from legacy
unstructured errors; manual session selection remains the safe fallback. Both
updated gateway and Android code are needed for full automatic stale-ID recovery.

## Local evidence

Executed in the isolated feature worktree; databases and backend clients in tests
are disposable. No live provider task or physical Samsung test was performed.

| Check | Result |
| --- | --- |
| Gateway suite, including six new typed-error/socket/persistence/ownership regressions | 200 passed |
| Phone unit tests, including four client-correlation tests and retained-history/preferences test | 286 passed |
| Shared Wear protocol / Wear unit tests | 30 / 1 passed |
| Phone and Wear lint and debug builds | Passed |
| Ruff lint and format (gateway) | Passed |
| Mypy CI active-source targets | 117 source files passed |
| Python compilation, product baseline, Bandit high-severity scan, diff whitespace | Passed |
| CI-pinned Gitleaks current-tree scan of tracked and new source (redacted output) | Zero findings |
| Separate disposable pre-effect classification reproduction (not the feature fix) | 2 passed |

Reproduction commands:

```sh
PYTHONPATH=bridge python -m pytest developer_gateway/tests -q
python -m ruff check developer_gateway
python -m ruff format --check developer_gateway
python -m mypy bridge/app developer_gateway tools/generate_ota_manifest.py tools/generate_product_release_manifest.py tools/verify_product_baseline.py
python -m compileall -q developer_gateway
python tools/verify_product_baseline.py --skip-ref
python -m bandit -q -r bridge/app developer_gateway -lll
git diff --check
```

In `android/jarvis-voice-client`, with SDK36/JDK17 and the repository's wake assets:

```sh
./gradlew --no-daemon :app:testDebugUnitTest :app:lintDebug :app:assembleDebug :wearprotocol:test :wear:testDebugUnitTest :wear:lintDebug :wear:assembleDebug
```

Local Python is 3.14.4, not evidence of Python 3.12 compatibility. The exact-head
protected GitHub results must be recorded in the PR evidence before recommending
merge. Gitleaks used the unchanged CI digest
`sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f`
with `detect --source=/repo --no-git --no-banner --redact --verbose=false` on a
disposable source snapshot, excluding ignored local build output/credentials.
A standalone mypy invocation lacking
the bridge import path initially failed import resolution; the CI target set above
passed. No source change or suppression was used to mask that environment error.

## Separate reliability follow-up — not implemented here

Introduce a narrow, explicit proven-pre-effect rejection type at specific patch
validation sites (including stale preimage and new redaction placeholders), and
return a sanitized structured negative tool result through the existing completed
call ledger. Do **not** catch arbitrary write/OSError/ValueError exceptions as safe.
Required tests: unchanged bytes on each preflight rejection; completed negative
call caching; changed call-identity rejection; and errors after write or before
journal completion remaining uncertain and never automatically replayed.

Also review the successful truncated full-file replacement separately: syntactic
or completeness safeguards must not undermine normal authorized source editing.
This PR changes neither `tools.py` nor `api_backend.py`; the original uncertain
call remains blocked for reconciliation.

## Release boundary

Guardian remains STOPPED/trusted. Production gateway, journal, original worktree,
Core, broker, credentials, Samsung APK, OTA and PRs #98/#99 are untouched. The
feature is for review only: no merge, deployment or APK installation. Subsequent
gateway rollout and data-preserving Android release require separate approval;
physical deleted-session acceptance remains outstanding.
