# Unified supervised development integration

Source repair based on `93bba11f1287328a7d297653dcf08ad9c38b0b44`.
No deployment, production task, owner signature or Guardian activation is
authorised by this document. Tests use disposable state and provider fixtures.

## Authority and handoffs

| Component | Authoritative input / transport | Output and next boundary | Recovery / authority |
| --- | --- | --- | --- |
| Core Improvement Centre | Recorded action receipts, failures and explicit owner requests; existing authenticated evidenced API | Candidate-local evidence hash, reviewed files, clean source SHA in existing Governor DB | Original evidence remains intact; explicit re-evaluation creates new provenance, never silently rebinds history |
| Governor source | Fixed read-only host DB corresponding to Core `/app/data/jarvis_improvement.db` | Eligible, current-source candidate; shared canonical scope policy | Unscoped/stale/unsupported records are skipped, not rewritten; legacy worker skips supervised-scoped queued work |
| Controller runtime | Root-owned installed entrypoint, fixed DB, configured call policy | Immutable task and approval request, original request ID | No second scheduler/DB; uncertain running work becomes reconciliation-required, never automatically retried |
| Publication | Root publisher, fixed Unix socket, actual gateway UID via SO_PEERCRED, SQLite read-only transaction | v1 exact approval envelope; v2 separate lifecycle status | Missing/corrupt DB is unavailable, not idle. Expired task is not signable. No schema writes during reads |
| Gateway / Android | Existing authenticated HTTP API; root socket peer; enrolled Android Keystore | Explicit owner-confirmed `jarvis-development-admission-v1` signature over exact task/source/scope/limits/generation | No HTTP RESUME; no approval button for invalid envelopes; signing is not Guardian activation |
| Operator / admission broker | Existing protected operator relay; real ECDSA verification; private ledger; unprivileged worker peer | One exact admitted receipt after separately authorised Guardian activation | Expiry, generation and STOP checked even on receipt retry; uncertain/untrusted observation denies; signed scope cannot enlarge policy |
| Bounded worker | Admission plus immutable task; no legacy deployment engine | Structured edits in private clone, actual call/cost ledger, check receipts | SDK retries/redirects disabled; each outbound call reserved before sending; ambiguous usage/outcome blocks replay |
| Local verification | Fixed tools in credential-free, read-only bubblewrap namespace | Real tests, Ruff, Bandit and parse checks; coordinator receipt outside jail | No host home, provider/GitHub credentials, Docker socket, network, writable Git metadata or arbitrary host commands |
| Trusted review | Actual HEAD/diff/paths plus private execution receipt | Durable `REVIEW_REQUIRED`, exact diff/evidence/usage | Model claims and pass booleans are insufficient. This is deterministic verification, not independent human approval |
| GitHub integration | Separately provisioned UID/token and a new purpose-specific enrolled-owner signature | Exact task branch and draft PR bound to source, full tree, diff and evidence | Same controller task; separate existing Records-format effect journal; uncertain remote writes require reconciliation, no duplicate retries |
| Integration / release | Separate human decision, independent review, protected CI/CodeQL and existing release gates | Explicit merge/deployment/OTA operations only | No merge/deploy/sign API exists in worker or PR handoff; no production operations in this repair |

## Implemented corrections

- One dependency-free scope policy is used by Core and the protected runtime.
  The installer copies its bytes into a root-owned manifest-pinned tools module;
  the privileged installation never imports mutable Core code. Canonical path
  spelling, security enforcement, authentication, deployment and verifier
  configuration exclusions apply to new files as well as existing files.
- The protected worker no longer imports the uninstalled legacy deployment
  engine. Its pure atomic structured-edit validator is shared with that engine
  without inheriting commit, PR, deployment or retry powers.
- Signed call limit defaults to one, configurable to at most four through
  protected service configuration (`JARVIS_SUPERVISED_MAX_CALLS`). The signed
  total remains at most $1 and 120 seconds, including validation. Actual usage
  is durably separate from worker invocations. No SDK retry is an extra free call.
- A failed deterministic check can feed one bounded repair iteration if another
  signed call and its worst-case cost remain. Unknown network/usage outcomes
  cannot do so. New files require exact signed paths. HEAD stays at the approved
  base; the generated result remains uncommitted.
- Token ceilings can be configured with `JARVIS_SUPERVISED_MAX_INPUT_TOKENS`
  and `JARVIS_SUPERVISED_MAX_OUTPUT_TOKENS` (defaults 12000/4000; reviewed maxima
  1000000/65536). Conservative prompt-byte accounting and the same total cost
  ceiling still apply. This does not prove model context availability or pricing.
- `JARVIS_SUPERVISED_MODEL` selects a canonical identifier from protected
  configuration, not candidate text. Generic rates require an exact matching
  `JARVIS_SUPERVISED_PRICING_MODEL`; legacy GPT53 rates cannot authorise another
  model. A configuration change cannot change a frozen task's signed model.
- Generated code cannot write the check receipt. The reviewer recomputes the
  actual diff and verifies provenance/check results instead of trusting claims.
- Gateway and Android distinguish idle, pending/expired, running, blocked,
  failed, review-pending and review-required. `APPROVAL_REQUESTED` is never
  presented as independently verified approval.
- Root coordination delegates admission and coding to the autonomy UID. Its
  reviewed namespace allow-list permits bubblewrap without adding host
  capabilities, writable home access, Docker access or Guardian authority.
  Unexpected failure while observing activation requires authoritative STOP,
  even before worker dispatch. An unconfirmed STOP is a critical failure.
- Timeout, cancellation and service termination kill and reap the admitted
  child before propagating failure. Since the root coordinator intentionally
  lacks CAP_KILL, a fixed helper drops to the exact worker UID/GID to signal
  only its unreaped child process group. No new capabilities are added.
- Upgrade workspace metadata uses no-follow file descriptors and inode checks.
  Symlinks, hardlinks, special files and swapped paths cannot make root change
  an unrelated target; rollback restores only the original descriptors, and
  uncertain path replacement requires reconciliation before reactivation.

## Live read-only inventory (2026-11-12)

| Surface | Observed | Meaning |
| --- | --- | --- |
| Canonical checkout and remote | `93bba11f1287328a7d297653dcf08ad9c38b0b44`, clean | Production source not changed |
| Core and Developer Gateway health | healthy; source `19d4158b627bc781a07bc879aa2095126b681834` | Subsequent Alpha47/48 changes were release changes; this difference alone does not prove stale approval handling |
| Core source mount | read-only `ca6b28a70e6786b2b6cddbc3db6ec815c68b0824-git` snapshot | Genuine stale source-binding prerequisite; requires separately authorised refresh after merge |
| Core persistent data | `/home/aaron/.local/share/jarvis-runtime/data` mounted at `/app/data` | Matches fixed Governor DB path; no data copied into Git |
| Protected bundle | `890ded5e01f331470355e9044fc3558dffa02ebfb381fc7247ba41edf5290f2d` | 32 readable installed file hashes/ownership passed; private configuration not readable by current account, not bypassed |
| Controller | inactive; root, supplementary group jarvis-autonomy | No approval window started; protected DB content not accessed without permission |
| Guardian | STOPPED, trusted; generation `b01a6dce-46d3-4068-8e9e-29e411b4470d` | Unchanged by this repair |
| Samsung | SM-G996B, `com.aaron.jarvisvoice`, Alpha48 / 190500 | Installed APK, not merely a build artifact |
| APK certificate | `009fd523f27cf94eb98917e17670804897e6378e5eccf1ce3ead680721691aac` | Read-only apksigner verification passed; does not establish the separate Keystore operator-key match |
| Protected Python | `/usr/bin/python3`: cryptography present; openai, pytest, Ruff and Bandit absent | Live execution dependency blocker, not resolved by the developer's test venv |

Historical HTTP 403 remains **UNKNOWN**. Corrected canonicalisation tests cannot
establish the original live rejection without its correlated retained audit
event. No new live request was submitted to manufacture such evidence.

## Deployment and rollback gates (not executed)

1. Review and merge this source PR only after exact-head protected checks. Record
   the resulting canonical SHA. Do not run an installer out of this writable
   development worktree. Produce and independently hash a tracked-only archive
   of the approved commit; stage it root-owned and non-writable by the worker or
   gateway before privileged execution. Existing user WIP and rollback snapshots
   must be retained.
2. Privileged owner preflight must reverify the exact installed `890ded…` bundle,
   every private manifest digest, STOP/trust/generation, audit continuity and
   operator enrollment. `CURRENT_BUNDLE` and `UPGRADE_BUNDLE` in the reviewed
   installer are authoritative; ordinary `--check` is not the old-version
   migration preflight. The immutable staged entrypoint is
   `/usr/bin/python3 -I <verified-stage>/tools/autonomy_broker_install.py --upgrade`.
   Destination fingerprint:
   `73e84548db287fb02daa86d9da8634ae62fec39f615322979e9e74dfd1f93489`.
   No privileged command is authorised by this PR. Never reuse a previous
   release's installer merely because its module names look similar.
   The installer's internal payload staging is not immutable Python source
   staging: imports execute first. Reuse the owner's separately reviewed source
   staging procedure; no safe staging CLI is supplied by this repository.
3. Migration validates the exact old 33-file inventory, stages the new payload,
   stops all affected broker roles before replacement, and replaces protected
   manifests. The destination adds two pure modules (35 installed records,
   including policy/private config). Existing policy, keys, DB, ledgers, jobs,
   audit and Guardian generation are retained. Failure rollback restores
   executable/configuration bytes and manifests, not application data. It then
   enables/starts all source-bundle sockets; it does **not** capture and restore
   each unit's prior enabled/active state or restart the controller. Therefore
   owner preflight must confirm all four source sockets are already enabled and
   active and the controller is inactive before using this procedure. These are
   deployment preconditions, not installer-enforced checks. Socket-activated
   services may normally remain inactive until a subsequent authorised request.
   A changed workspace inode blocks reactivation and requires reconciliation.
   An interrupted/uncertain upgrade must not be repeatedly invoked or repaired
   by manually rewriting manifests.
4. Provision reviewed worker dependencies under a root-owned immutable Python
   environment and verify bubblewrap under the actual systemd sandbox before any
   task. Do not lend the worker Docker, sudo or a home-directory test venv. Secure
   provider credential provisioning, model availability, context limits and
   independently verified pricing remain owner prerequisites, not inferred from
   a numeric rate. The existing roughly 484 KB ai_engine/test scope exceeds the
   default conservative prompt ceiling: select/configure a genuinely feasible
   scope/token budget without silently truncating evidence or exceeding $1.
5. Deploy compatible Core (including shared policy) and refresh its tracked-only
   read-only source snapshot, preserving all persistent mounts and old image /
   snapshot. Deploy Gateway via its existing clean-canonical
   `bash developer_gateway/install.sh`, retaining previous release symlink and
   user unit for rollback. Verify authenticated v2 status, source binding, safe
   empty/expired states and no task creation; publication remains a Unix socket.
6. Android changes require separate normal signed release/update approval. Keep
   the same package/certificate, operator Keystore and app data. Never uninstall
   or clear data. Verify the phone's actual enrolled operator key independently
   of its APK certificate before starting the short approval window.
7. PR integration is deliberately **not provisioned** by the broker installer:
   it needs a separate non-root/non-autonomy identity, fixed root-owned
   `/etc/jarvis-development-integration/policy.json`, narrow access to verified
   proposal/receipt/task progress, fixed LoadCredential token and its private
   `/var/lib/jarvis-development-integration` effect journal. The owner must sign
   domain `jarvis-development-integration-v1`, action `create_development_pr`.
   The current Android development-approval UI does not sign that distinct
   capability. No approval domain or credential is silently reused. Independent
   reviewer enrollment and protected review/CI remain required for integration.
8. Only after all live prerequisites pass: prepare a genuinely current-source
   candidate through the official API, then separately authorise publication,
   genuine Samsung signing and protected Guardian activation. The trial must
   end at REVIEW_REQUIRED and confirmed STOP. No paid trial occurred here.

## Evidence scope

Final local validation (2026-11-12):

- Full Core/tools/Gateway suite: 2824 passed, 143 subtests passed, 8 explicit
  opt-in skips, one existing Starlette/httpx deprecation warning. Five skipped
  protected-publication/process cases passed in their separately enabled runs;
  three unrelated legacy real-deployment/rollback rehearsals were not run.
- Actual controller process rehearsal: four passed (repair, ambiguous provider
  disconnect, unavailable sandbox, SIGTERM during transmission). Every case
  confirms STOP/trust and no provider replay on restart. Successful repair uses
  two explicitly signed calls; production default remains one.
- Exact-version installer: 42 passed, including metadata symlink/hardlink/FIFO,
  inode-swap, migration, modified/unknown source and rollback cases.
- Android Phone 326, Wear 1, shared protocol 30 passed; both lint checks and
  unsigned debug builds passed. No production signing or installation occurred.
- Home Assistant 24 unique checks, release/dashboard/package validation passed
  in disposable storage. No Home Assistant action was executed.
- Ruff/format, Mypy (126 CI-scoped files plus 8 protected modules), high-severity
  Bandit, compilation, product baseline, pinned actionlint and dependency audit
  passed. systemd unit verification passed with two unrelated host deprecation
  warnings. Pinned Gitleaks found no leaks in the clean candidate source.

A later scan of the test export included generated `.pyc` files and found three
private-key-pattern matches: two from the existing credential redactor's PEM
boundary constants, one from its dummy `very-secret` regression string. Source
inspection confirmed these contain no usable key. No suppression was added;
the clean-source scan passed. Safe process JSON receipts and full test logs are
retained under `/var/tmp/jarvis-unified-integration-evidence` on the audit host.
Exact-head GitHub results belong to the PR, not this pre-commit record.

Focused suites exercise signed approval and actual socket peer credentials,
read-only publication, real isolated verification, failed-test repair, controlled
HTTP transport call counts, durable costs/recovery, draft-PR effect deduplication
and exact-version migration/rollback. Final counts and exact-head GitHub checks
are recorded in the PR. Full live autonomy is **not demonstrated** by fixtures.
Source-level deterministic review also cannot replace independent human review
of the adequacy and intent of model-edited tests.
The fresh per-invocation completion marker rejects a static forged stdout
success followed by premature process exit. It is a correlation measure, not
an authentication boundary inside an arbitrary Python interpreter. Hostile
test code can manipulate in-process testing semantics; a successful check
receipt must not be treated as independent human approval or proof of safety.

The process rehearsal starts the real installed-style entrypoint, not manual
library transitions. It uses a disposable Docker namespace, actual UID 1000
gateway and UID 600 worker sockets, real ECDSA and Guardian generation changes,
the SDK against a local HTTP fixture, actual bubblewrap verification, a failing
first patch followed by an authorised repair, and restart/no-replay assertions.
Its container needs a host-approved user-namespace AppArmor profile (`runc` on
the audit host); it neither installs a profile nor proves the live systemd
sandbox has passed acceptance. Revalidate that sandbox after separately
authorised installation. All test signatures, state and provider traffic are
disposable; none are live owner approvals or paid requests.
