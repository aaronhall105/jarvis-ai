# Developer owned-file recovery

## Scope and reproduction (2026-10-28)

Base: `330b2d1d6de1bc514496130009bf0470a8d76b70`.
Branch: `jarvis/developer-owned-file-recovery`.
All tests use disposable Git worktrees and SQLite journals. No live gateway,
database, APK, Guardian, credentials, OTA or routing changes are required.

Before modifying source, the first two tests in
`developer_gateway/tests/test_owned_source.py` failed on the canonical base:

1. Creating `tools/developer_session_diagnostics.py` with `patch_file` and then
   reading it raised `Only tracked source may be read`. The read/search inventory
   used only `git ls-files` tracked entries.
2. A `.env` read correctly denied by path policy became a failed turn and
   recoverable tool interruption. Generic exception handling did not distinguish
   a deterministic pre-effect rejection from a genuine interrupted operation.

## Correction

- Read/search may include nonignored untracked source only in the session-owned
  path, on its exact expected Developer branch, sharing the canonical Git common
  directory. Canonical-root reads remain tracked-only.
- Conservative new-source extension and sensitive-name filters do not expose
  arbitrary runtime, dotfile or credential material. Existing protected path,
  containment, regular-file/hardlink and size checks remain enforced. Text must
  be UTF-8 and contain no NUL bytes.
- Directory-descriptor reads with no-follow semantics prevent parent/leaf symlink
  swaps from redirecting a validated read. Reads are bounded and nonblocking.
- An explicit pre-effect `ToolPolicyError` produces a completed, cached negative
  result with `code=file_policy_rejected` and `retryable=false`. Android receives
  the existing failed tool-activity status plus a safe explanatory message.
- Three policy denials in one turn stop it with a persistent policy-blocked
  reason. `turn/retry` cannot restart that loop; a new instruction can start a new
  turn without erasing prior history. No Android protocol replacement is needed.
- Arbitrary execution exceptions are NOT converted to safe denials. Genuine read
  interruptions remain recoverable, uncertain effects remain fail-closed, and
  completed-call caching and call-identity checks are unchanged. No journal
  schema migration or approval-policy change is needed.

## Validation

Initial red tests: **2 failed**, before implementation.
New focused regressions: **28 passed**. Wider gateway suite: **179 passed**.
The original create/read/search multi-tool flow completes without `git add -N`,
leaves the new file untracked, reports completed activity and restores the saved
conversation after restart. Negative tests cover canonical/foreign workspace,
repository/branch ownership, ignored/sensitive/protected paths, symlinks (including
concurrent swaps), hardlinks, FIFOs, missing paths, oversized/binary/malformed
files, cached failures, identity conflict and nonretryable policy-loop recovery.
Existing recovery tests cover interrupted reads, uncertain writes, approvals,
multi-tool reconnects and completed-effect replay prevention.

Reproduce locally with the installed test environment:

```sh
PYTHONPATH=bridge python -m pytest developer_gateway/tests -q
python -m compileall -q developer_gateway
python -m ruff check bridge/app bridge/tests developer_gateway tools
python -m ruff format --check bridge/app bridge/tests developer_gateway tools
python -m mypy bridge/app developer_gateway tools/generate_ota_manifest.py tools/generate_product_release_manifest.py tools/verify_product_baseline.py
python -m bandit -q -r bridge/app developer_gateway -lll
python tools/verify_product_baseline.py --skip-ref
git diff --check
```

Local Python is **3.14.4**, not proof of Python 3.12 compatibility. Exact-head
protected CI must independently run the repository's Python 3.12 gate. CI and
CodeQL links/results will be recorded on the PR after completion, not assumed.

Pinned Gitleaks current-tree scanner:
`zricethezav/gitleaks@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f`,
`detect --source=/repo --no-git --no-banner --redact --verbose=false`.
Scan the complete Git source snapshot including the new tests. A preliminary
scan of the test-running directory additionally found two private-key-pattern
matches in ignored Python bytecode for unchanged `connectors/credentials.py`.
These are compiled PEM delimiter constants (source lines 40–43), not keys;
source comments identify the existing literal-marker exceptions. No scanner
configuration or source exception was added by this repair.

An initial wider pytest/mypy invocation omitted the repository's import-path
setup and could not resolve `app.research_engine`. The corrected CI-equivalent
commands pass; no source change was made to hide that environment error.

## Limits and rollout boundary

- No real-provider spending or physical Samsung testing is claimed. Deterministic
  provider fixtures drive the actual tool runner, persisted jobs and wire events.
- Older Android clients may still draw Retry Task on a terminal failed turn;
  the server rejects that action safely with a changed-request explanation.
- Untracked dotfiles, credential-like names and unsupported extensions remain
  intentionally unavailable. This is not unrestricted filesystem access.
- No production deployment, merge or autonomy activation is authorized here.
  Live service validation requires a separate approved rollout.
