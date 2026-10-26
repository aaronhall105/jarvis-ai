# Android Developer self-repair continuation — 2026-10-25

## Preserved source and evidence

Worktree: `/home/aaron/.local/share/jarvis-developer/agent/worktrees/api_166a0e7b58294991b78a5a61578d8be8`.
Branch: `jarvis/developer-166a0e7b58294991b78a5a61578d8be8`.
Base remains `429cee8ac2d8efdc5d7c99805699edae78e9a85d`; no reset/rebase/merge.
Recovered initial diff: 65 additions/21 deletions in api_backend.py/state.py.
The original two patch calls remain recorded, unchanged, in the live journal.
Only read-only SQL metadata inspection was used against that journal; never open
the production database with the State constructor to investigate it.

The saved self-repair turn is `9436b4543cae4d2b9c25fa1f98099007`.
All 52 tool calls completed: 2 worktree calls, 1 repository inspection, 20 searches,
27 file reads, 2 patches. No verify/test, commit, push or PR tool ran.
Saved status/error: failed / `Turn failed; state preserved`. Pending response is
absent, and the next provider input is the last completed patch's tool result.
This places the failure in provider continuation, not an unfinished patch.
The installed gateway continued to run its original 429cee8 release, not this WIP.

The original `api_8d01def73395…` failed PR-status thread no longer exists in the
journal. A thread-delete audit event precedes the repair session. Its historical
exception/timeout/network cause cannot be reconstructed exactly. The gateway did
not persist the exception class/body for generic turn failures. RuntimeError and
ValueError socket warnings alone do not prove a Tailscale cause.

## Confirmed defects and fixes

1. **Read-only validation failures became uncertain effects.** `pr_status` with
   no task branch raises before any GitHub operation in the old runner; the generic
   tool catch then labels it uncertain. This reproduces the reported behavior
   without a network interruption. It now returns an actionable negative tool
   result. Genuine interrupted reads remain resumable, and legacy uncertain reads
   migrate to recoverable. Identity/turn/backend/schema/path checks still apply.
2. **The saved repair contained invalid Python.** Its full-file replacement copied
   `AsyncOpenAI([REDACTED] max_retries=0, ...)` from a redacted source read. The
   redactor incorrectly treated `api_key=self.key` as a literal credential.
   Restored the non-secret expression. Lexical handling preserves self.attribute
   code references, never quoted/comment credentials or actual known keys.
   Structured results are redacted at string leaves rather than mutating serialized
   JSON. A file replacement cannot introduce new redaction placeholders.
3. **Unsafe read classification.** Jarvis's patch included verify as read-only,
   although format and script profiles can write. It is explicitly excluded.
   Interrupted writes, tests/scripts, Git and PR effects remain uncertain and
   require reconciliation; forged recoverable state cannot grant replay authority.
4. **SDK stream errors bypassed retry/error normalization.** Real SDK tests prove
   HTTPX read/protocol errors and SSE APIError envelopes can escape the previous
   catches. Transport/server failures retry boundedly with the same recorded tool
   results; no partial streamed tool is executed. Other errors pause/fail safely.
   Credits/quota failures receive a fixed actionable message and no automatic
   retries. No arbitrary provider body enters UI/history/log diagnostics.
5. **Activity/history and lifecycle gaps.** Persist normalized tool activity,
   reconstruct older tool history from the ledger without source/argument/output
   disclosure, preserve error cards and retryable status, synchronize stopped-turn
   status, and release the journal lock on shutdown. The original repair removed
   State.close; that removal was corrected. Router restart creates a fresh owner.
6. **Retry/finalization accounting.** Replayed completed calls do not repeatedly
   advance no-progress/failure guards. Missing/invalid usage cannot destroy a valid
   final answer; totals explicitly report incomplete usage instead of inventing it.
7. **Android rendering.** Restored tool records now render in history; live/history
   cards show normalized messages; resumed status is not left as Connected/Completed.
   Existing common final-message parser and retry control remain. The exact
   MainActivity baseline digest is updated for this reviewed UI diff, not removed.
8. **Test isolation.** Gateway tests explicitly use temporary state and no real
   credential. An initial old health test attempted the real state path and was
   correctly refused by its existing owner lock before journal mutation.

These changes retain the original repair's recovery/item-reporting design rather
than replace the Developer architecture. No unrestricted tools or approvals added.

## Historical live API finding and limitation — October 25

**Superseded by the successful October 26 validation below.** The earlier failure
remains recorded here as historical evidence, not a current credit blocker.

The disposable live probe failed before any tool execution. A minimal Responses
request using the same existing dedicated credential returned:

> You have no credits remaining.

No key was printed/copied, no billing settings were accessed/changed, and no Core
credential was read. This is an observed current provider-account blocker, not a
ChatGPT/Codex usage window or a quota introduced by this repair. No further live
calls should run until the operator restores API credit/access.

This SDK failure would fall through the old generic turn catch and is consistent
with the historical failure after 52 calls, but the historical exception was not
retained: do NOT assert that exhausted credits definitively caused that older turn.

Probe evidence is retained at `/tmp/jarvis-developer-recovery-probe-zkzxkcrs`:
temporary fixture source/journal, zero executed tools. Do not call this a successful
live engineering cycle. A successful real-provider comparable task remains BLOCKED.
Cached `api_backend_healthy` on the unchanged live service is not a billing probe.

After access is restored, explicitly rerun the bounded isolated probe:

```sh
PYTHONPATH=. JARVIS_DEVELOPER_API_KEY_FILE=/home/aaron/.config/jarvis/developer-api-key /home/aaron/.venvs/jarvis-phase2/bin/python tools/certify_developer_recovery.py --live
```

It uses only a disposable local Git fixture, the existing API backend and restricted
offline Bubblewrap verification. It approves no protected operation. It checks one
patch, focused pytest, model continuation/final, disconnected-client survival,
restart history/ledger equality and unchanged fixture source. It never targets the
Jarvis repository or live Developer journal. Fixture evidence is retained, not
silently deleted. This is not deployment, a phone test, or permission for autonomous
engineering; Guardian must remain trusted STOPPED.

## Validation and what remains unproven

- Deterministic tests cover legacy read reconciliation, unknown-write denial,
  stable call identity, approvals unchanged, multi-tool partial completion/restart,
  real SDK transport/SSE errors, credits exhaustion/retry, finalization/usage,
  cancellation/lock release, old history, source redaction and placeholder refusal.
- Real restricted-sandbox fixture test performs worktree/read/patch/compile/diff
  and returns results to a scripted provider. Existing WebSocket disconnect/reconnect
  test remains green. Scripted provider tests are not real-provider acceptance.
- Android local tests: Phone261, Wear1, protocol30; lintDebug/assembleDebug PASS.
  No APK installed/signed/published and no physical UI fix claimed.
- Full Python and final focused/static/security totals are recorded in the handoff
  below. They are LOCAL evidence, not protected CI on a new commit.

## PR preparation / next authority boundary

### Final local validation

| Gate | Result |
| --- | --- |
| Full Python: bridge/tests tools/tests developer_gateway/tests | 1773 passed, 3 skipped, 143 subtests; one existing Starlette/httpx deprecation warning |
| Focused Developer suite (subset above) | 105 passed, including 45 new recovery cases |
| Phone / Wear / protocol JVM tests | 261 / 1 / 30 passed; zero failures/errors/skips |
| Phone lintDebug / assembleDebug | PASS; local debug artifact only, never installed |
| Ruff check / format | PASS, 219 files formatted |
| Active-source mypy + probe | PASS, 107 source files |
| Compileall / high-severity Bandit | PASS |
| Dependency pip-audit | No known vulnerabilities found |
| Workflow actionlint / product baseline / diff check | PASS |
| Source-only Gitleaks 8.30.1 | No findings, 8.30MB; includes new untracked tests/probe/docs, excludes runtime secrets/builds |
| Real SDK / sandbox / reconnect / restart fixtures | PASS deterministically; do not confuse with live-provider completion |
| Live provider comparable repair | BLOCKED: account reports no credits; zero fixture tools executed |
| New protected CI / physical Android UI | NOT RUN: no new commit/PR or APK installation authorized/performed |

Full results: `/tmp/jarvis-developer-self-repair-validation.4cbxE2/final-pytest.log`.
Android build: `/tmp/jarvis-developer-self-repair-android.log` and local Gradle XML.
The original live thread still has status failed, updatedAt1792950931 and all52
completed calls, unchanged. Core remains healthy at8b8d7b0; Developer healthy at
429cee8 with API default/configured. These liveness fields do not override the
observed provider-credit blocker. Guardian remains STOPPED/trusted at generation
0c8b01fb-1347-4d2b-83b7-9a9c1e80ef89.

Suggested title: **Fix Developer read reconciliation and streamed-turn recovery**.
PR scope is this repair only, not autonomy/observer/signing/release work.

Leave the recovered branch/WIP intact. Its base predates canonical alpha40 d4013a0.
Preparing a current-base integration needs explicit review; do not overwrite the
alpha40 dashboard or replace its baseline hash with the older whole file. No branch
merge is authorized here. Resolve by applying the narrow repair to the approved
base in a later authorized integration step, retaining this original evidence.

The structured Developer policy approval-gates commits, pushes and PR mutations.
No exact-operation approval was granted by this saved session. This continuation
therefore does not bypass those gates via host Git/gh commands: no commit, push or
new PR was performed. This document is the local PR draft and review checklist.
PR98 and PR99 remain separate, unchanged/unmerged.

Required next steps: restore provider credit/access, rerun the live fixture,
authorize commit/current-base PR preparation and protected CI, then separately
authorize a Developer gateway deployment and any Android update. Until deployment,
the running gateway still contains the old behavior; merely retrying the original
phone thread does not run the repaired code. Never replay uncertain writes manually.

## Safety record

Guardian STOPPED/trusted. No live gateway restart, Core/Developer deployment,
Guardian/broker change, key/signing change, phone installation/data clear, OTA,
canonical merge, or autonomy/Phase2 activation. Original live task/session/worktree
and all unrelated feature/recovery/signing WIP preserved.

References used for provider boundary: [Responses streaming](https://developers.openai.com/api/docs/guides/streaming-responses)
and [function calling](https://developers.openai.com/api/docs/guides/function-calling),
plus the installed Python SDK's AsyncStream implementation. Only completed
responses supply executable tool calls; SDK stream exceptions must be handled too.

## Resumed real-provider validation — 2026-10-26

### Source and authority

Preserved the same dirty repair worktree and base `429cee8ac2d8efdc5d7c99805699edae78e9a85d`.
No production implementation change was needed to pass the restored-provider tests.
This continuation added `tools/certify_developer_read_recovery.py`, extended the
existing live fixture with a representative PR-status repair and cached-patch
replay proof, and added two harness spending-bound regression tests. The previous
gateway/Android fixes were retained unchanged. No commit/push/PR performed.

Read-only GitHub inspection reconfirmed canonical head
`d4013a0137a5b88e3e401627084b35d58c8c813a`. PR98 remains OPEN at
`ac3f3f1e2ac442fa4ded96d1229e51481b4b6636`; PR99 remains OPEN at
`a586125786fa7971138a9cbd1bb72981f059eb5d`. Neither was altered.

### Provider access and spending

Used the configured dedicated Developer credential mechanism, provider host
`api.openai.com`, unchanged model `gpt-5.4`. Model retrieval succeeded, then a
minimal real Responses request completed (9 input / 5 output tokens). No key value,
account identifier, balance, provider prompt or response body was logged.

| Live probe | Input tokens | Output tokens | Outcome |
| --- | ---: | ---: | --- |
| Access check | 9 | 5 | PASS |
| Existing arithmetic repair fixture | 8,983 | 339 | PASS |
| Read-only WebSocket/GitHub inspection, three turns | 18,534 | 527 | PASS |
| Representative PR-status self-repair fixture | 11,277 | 376 | PASS |
| Total | 38,803 | 1,247 | 40,050 tokens |

Usage fields were complete. No dollar cost or remaining balance is inferred from
token totals. The two newer probes used 8 provider requests each. The first
existing probe was bounded by its 180-second deadline; the extended harness also
caps requests at 12 and output at 2,048 tokens per request. The read probe has a
120-second task deadline and one transient retry, with a separate cap per backend
lifecycle. These are certification-harness limits, not new production quotas.

### Reproducible live evidence

1. `/tmp/jarvis-developer-recovery-probe-46mnq7nx`: real model completed six tools:
   create_worktree1, read_file2, patch_file1, repository1, verify1. Focused pytest
   **1 passed**. Client sink disconnected during tool start; job finished, then
   a fresh backend owner recovered identical history/ledger. Original fixture
   source was unchanged.
2. `/tmp/jarvis-developer-read-recovery-ziz458cg`: repaired FastAPI application ran
   in an isolated TestClient lifespan with a fresh test auth token and private
   state. Actual OpenAI SDK requests, real repository read/search/status and real
   `gh pr view` executed. Independent harness guard rejected all non-read tools.
   First `pr_status` without a task branch completed with the expected actionable
   negative result. The second inspected the **existing repair branch** and GitHub
   reported no PR (exit1, not an authentication failure). Both ledger rows remained
   completed, not uncertain. Five tools total; zero write tools/worktrees.
   Actual WebSocket disconnected during item/started, reconnected to a completed
   session, then application lifespan stop/start restored identical history and
   ledger. A third real-provider turn continued from that persisted session with
   no additional tools. Tool start/completion, assistant delta and final turn
   completion events were observed. This is in-process WebSocket application
   evidence, not a physical Android/Tailscale-network acceptance claim.
3. `/tmp/jarvis-developer-recovery-probe-eoid26x0`: representative reproduction of
   the old no-branch `pr_status` exception in a disposable Python module. The real
   model inspected source/tests, created its isolated worktree, patched only that
   module once, inspected the diff and ran the fixed offline pytest profile.
   **2 unchanged tests passed**: no-branch returns a completed read result without
   external inspection, existing branch inspects exactly once. Seven tools total;
   no commit/push/PR/deploy. Model continued after results and produced final.
   Reopened backend, replayed the exact recorded completed patch call with an
   execution trap installed: stored result returned, trap never invoked, file hash
   unchanged, ledger row count unchanged. Zero unresolved operations.

The last two fixture safety assertions (exact patch target/unchanged test/fixed
pytest arguments and recognized GitHub no-PR output) were additionally checked
against the retained journals without spending another provider turn. All three
journals were checked against the configured key in memory; none contained it.
No fixture evidence was deleted. A real narrow self-repair cycle now passes; this
is not proof of arbitrary large repairs, protected Git writes, deployment, or all
future provider failures. Original deleted PR-status thread remains unavailable,
so its literal historical prompt and precise triggering exception are not claimed
to have been replayed. Both reproducible PR-status failure cases were exercised.

Commands from this worktree, using the existing private credential path:

```sh
PYTHONPATH=. JARVIS_DEVELOPER_API_KEY_FILE=/home/aaron/.config/jarvis/developer-api-key /home/aaron/.venvs/jarvis-phase2/bin/python tools/certify_developer_read_recovery.py --live
PYTHONPATH=. JARVIS_DEVELOPER_API_KEY_FILE=/home/aaron/.config/jarvis/developer-api-key /home/aaron/.venvs/jarvis-phase2/bin/python tools/certify_developer_recovery.py --live --reconciliation
```

### Refreshed local matrix

| Gate | October 26 result |
| --- | --- |
| Developer unit/integration/SDK/sandbox/reconnect/security regressions | **107 passed**, including 2 new probe-bound tests; existing Starlette/httpx deprecation warning |
| Live configured API and model access | PASS |
| Read/search/repository/pr_status, multi-tool continuation/final | PASS |
| Real-provider client disconnect, application restart, persisted history | PASS |
| Supervised isolated source edit/test/final | PASS |
| Completed-patch replay, no duplicate effects/unresolved calls | PASS |
| Ruff check / format | PASS |
| Mypy | PASS using `--explicit-package-bases --follow-imports=silent`; namespace flag prevents imported tools module being discovered under two names |
| Compileall / high-severity Bandit | PASS |
| Current source-only Gitleaks / diff check | PASS; Gitleaks8.30.1 scanned8.32MB with zero findings, including new untracked probes/tests/docs; snapshot `/tmp/jarvis-developer-resume-scan.gGKQsl` |
| Android and full Python suites | Reused unchanged-code October25 evidence above; not repeated unnecessarily |
| Current-base integration / new protected CI | NOT RUN; requires next approved integration/commit/PR step |
| Physical Android UI / deployed repair | NOT RUN; no APK or service change authorized |

### Remaining integration steps and safety

The repaired code is **ready for current-base integration review**, not deployment.
The provider-credit blocker is resolved. Current production still runs old code.

1. Authorize a separate narrow integration branch/worktree from freshly verified
   canonical `d4013a0` (or its reviewed successor). Preserve this original dirty
   repair worktree and journals. Do not merge its old base wholesale.
2. Apply only Developer recovery/redaction/lifecycle changes, tests, probes and the
   narrow Android activity-rendering hunks. Preserve alpha40 autonomy/dashboard
   additions. Recompute baseline digest from the integrated MainActivity, never
   copy this old-base whole file or its old-base hash over current production.
3. Review integrated diff; rerun Developer/security/static and Android changed-area
   tests, then required full gates on that exact base. Authorize commit/push and
   one repair PR; obtain protected CI/security review at its exact head.
4. Separately authorize merge and Developer deployment/rollback planning. Android
   UI shipping requires its own release/installation approval. Do not manually
   replay uncertain writes from old sessions.

Final live health checks: Guardian **STOPPED/trusted=true**, same generation;
Core healthy at `8b8d7b08160e3ea36f9d944e2ab2528b37dddc0a`; installed Developer
healthy at `429cee8ac2d8efdc5d7c99805699edae78e9a85d`, API configured/healthy/default.
No production service restart, deployment, phone/APK/data change, signing,
publication, OTA promotion, Guardian modification, PR98/99 mutation or autonomy
activation occurred. Local fixture Git initialization/worktrees/patches were
confined to the disposable validation directories above.
