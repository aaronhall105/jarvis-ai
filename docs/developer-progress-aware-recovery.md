# Developer progress-aware repetition recovery

## Scope and provenance (2026-10-28)

Canonical base: `1216f10091e9d82168d4fb7b5f18c1ae200cf0e5`.
Branch: `jarvis/developer-progress-aware-recovery`.
Isolated worktree: `/home/aaron/jarvis-developer-progress-aware-recovery-worktree`.
Only gateway orchestration, its tests and this evidence are changed. No Android
feature implementation, production deployment, journal migration or approval
policy change is included.

## Read-only production forensics

The two requested sessions were inspected with SQLite `mode=ro`. No raw tool
arguments, source excerpts, conversation text or credentials are included here.

| Session suffix | Completed calls | Negative / uncertain outcomes | Trigger |
| --- | ---: | --- | --- |
| `820f5d93729241a08d95d3fccf4d0e04` | 65 | 0 / 0 | fourth `read_file` signature occurrence |
| `e6f78cfe75ed41aa9aa5a2924ee8aef6` | 55 | 0 / 0 | fourth `read_file` signature occurrence |

Both triggered the same canonical argument signature SHA-256:
`ae9765fdb7f7df3b9e468bc75399be22bda2128e626b41a5239142a083cb45c8`.
All three prior results had the same whole-file SHA-256:
`c2eb3e554157a7bc7f02fb46a533af4659e6b82874984686ae6a57db75d613f4`.
The prior call positions were 12/55/56 and 8/19/47 respectively. The recorded
patches did not modify that file. These were redundant source observations,
not failed tools; other work occurred between visits. Search was used in both
sessions but did not trigger either incident.

The old guard counted the same raw tool signature over the entire turn and
aborted before claiming its fourth call, irrespective of intervening evidence
or source changes. This explains the absence of failed journal operations.
It raised `ProviderUnavailable`, incorrectly classifying the stop as an outage.

Persisted latest function outputs match their completed journal results and
both sessions retain a previous response ID. All current-turn calls were
accounted for. There is no evidence of missing latest outputs. Provider-side
compaction history and the model's reasons for rereading are not retained, so
neither compaction nor missing context is asserted as the incident's cause.

Session/ledger digests were unchanged on the final read-only recheck:

- First session: `03bb1f30302d3989344c90fbfd637b00467a468f4c405c13a14abd19a4b0fee1`;
  calls: `6207a0ec297b9dfa73f4cc430c215bc0b4196d983e6fafe511b37d1d245b74b2`.
- Second session: `fbe0ef26e751e45f9637a50995dc5068f2911efa409789ed5654e56dd7a023f4`;
  calls: `fc4e490437f5fe32d02da527f2d1b98e79805bb09789fde948ac73727af9c5cf`.

## Preserved agent work

Both original worktrees and branches remain unmodified.

- `jarvis/developer-820f5d93729241a08d95d3fccf4d0e04`: one untracked
  `tools/jarvis_owned_file_live_smoke.py`, a small source-read smoke helper, not
  a completed Android recovery feature.
- `jarvis/developer-e6f78cfe75ed41aa9aa5a2924ee8aef6`: modified
  `developer_gateway/api_backend.py`, `developer_gateway/app.py`, and
  `developer_gateway/tests/test_gateway.py`. Its binary diff SHA-256 remains
  `99954e9171d555ea3353b2e1bd008011309a765956e3bb1116e75a400a00ef25`.

The second worktree has useful ideas for typed lookup errors and preserving a
WebSocket connection on a rejected request. However, it also broadly replaces
provider/recovery/approval machinery and changes observer startup/API behavior.
It is incomplete and unsafe to integrate without a separate review. Syntax was
inspected, but that implementation was not executed or copied into this repair.
Neither worktree contains Android client source changes.

## Correction

- Preserve a durable per-turn set of successful operation/result observations.
  New evidence resets stagnation; three consecutive redundant observations stop
  the turn. Alternating already-known results cannot evade this guard. The
  repetition threshold is not raised, and effectful-operation repetition limits
  remain enforced before execution.
- Reuse only a successful same-turn `read_file` result with the same canonical
  call signature and current whole-file hash. Every reuse revalidates workspace
  ownership, branch/repository identity, allowed inventory, path policy and safe
  bounded bytes. Dirty and untracked edits invalidate results without Git adds.
  Search is always recomputed because its inventory may have changed.
- Keep exact-call identity and completed-result replay semantics unchanged.
  No effectful cache or automatic uncertain-write replay is introduced.
- Supply bounded, content-free operation/result metadata and the existing goal
  on provider continuations. Results still use normal function outputs and
  previous-response chaining; the compaction contract is unchanged.
- Persist observation accounting per response cycle and call ID. A restart
  replaying a pending response does not double-count progress. A provider that
  repeatedly reissues the same ID in later cycles is still bounded.
- Classify loop stops as `tool_loop`, preserve successful tool activity/history,
  and reject blind Retry Task with changed-objective guidance. A new turn may
  continue without erasing history. Seen call IDs reset with their per-turn
  counters, fixing inconsistent bookkeeping when an ID recurs in a new turn.
- Keep approval controls, policy denials, read-interruption recovery and
  fail-closed uncertain side effects intact. No SQLite schema changes.

## Executed local validation

Before implementation, four new deterministic cases failed on unchanged source:
read/search revisits separated by new evidence, reads after real patches, and
the erroneous provider-outage category on an unchanged-read loop.

After correction:

- New progress suite: **14 passed**.
- Entire Developer gateway suite: **193 passed**, including the PR #102 owned
  source regressions and existing recovery, approval and sandbox tests.
- Compilation, Ruff correctness/format, mypy (116 source files), Bandit
  high-severity scan, product-baseline integrity and `git diff --check`: **PASS**.
- Pinned CI Gitleaks current-tree source scan: **zero findings**. The snapshot
  includes tracked and new source files, excludes ignored generated caches, and
  uses the unchanged scanner configuration.

Local Python: **3.14.4**. Python 3.12 and protected CI are separate evidence;
their exact-head results must be recorded on the PR, not inferred from local
tests. A harmless existing Starlette TestClient deprecation warning occurred.

Reproduce:

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

Pinned scanner:
`zricethezav/gitleaks@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f`,
`detect --source=/repo --no-git --no-banner --redact --verbose=false`.

## Safety review and limitations

Branch/PR events run CI and CodeQL only. The existing signing/publication workflow
requires a `v*` tag; the push must use an explicit branch ref and no tags.
Guardian was independently checked **STOPPED / trusted=true**. PRs #98/#99 remain
open and unchanged. No production service, live database, original worktree,
Android app, signing material, OTA feed or routing configuration was modified.

Tests use disposable repositories/journals and deterministic provider responses.
The compaction fixture checks continuation compatibility, not live provider
compaction behavior. No new real-provider spending or physical Samsung execution
is claimed. Hash-based progress recognizes new evidence, not its semantic value;
it cannot guarantee a model chooses useful work. The existing emergency task
timeout and failure limits remain in force. Legacy Android clients may still
offer Retry Task; the server refuses a terminal loop safely and explains why.

Merge and production deployment require separate authorization. Gateway restart,
live smoke tests and executable rollback are not performed by this repair.
