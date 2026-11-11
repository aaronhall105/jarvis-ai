# Self-improvement repair review

Base: `0fdc16f5987d8f1b17c7ec29819caf57ff881134`.
This is a draft source repair, not deployment or execution approval.

## Corrections and evidence boundaries

- Feedback attribution now follows a recorded write receipt in the latest
  interaction, or an exact receipt reference from the existing explanation
  tool. It does not infer an executed light/notification/media action from
  conversational wording. Missing, conflicting, multiple or malformed receipts
  remain reviewable without attributing a write to a read-only interaction.
  Principal and conversation boundaries are enforced. Identical event retries
  preserve the original evidence; distinct interactions and numeric corrections
  are not deduplicated together. Concurrent queue creation is serialized.
- The Governor uses the same read-only classification for review and selection.
  Unscoped feedback is not automatically a software defect. Selection requires
  candidate-local evidence hashes, a current clean source revision, tracked
  regular files, canonical permitted paths and an explicit current-source review.
  The 41-record regression fixture remains byte-for-byte unchanged by review.
  No production candidates were read, classified or changed in this repair.
- Historical source bindings are never updated in place. An authenticated
  reviewer can explicitly reevaluate an unarchived queued candidate through
  `/api/improvement/request/evidenced`. A new candidate links the exact original
  scope and its hash; the old candidate, failure and existing audit history are
  preserved. Approved/running candidates cannot be replaced through this route.
  Scope and its audit entry commit or roll back together. Five historical-source
  fixture records, dirty/deleted source files and changed evidence are covered.
- Two Android/Python signing incompatibilities are independently reproduced:
  Android framework JSON escapes `/` in path arrays, while Python canonical JSON
  does not; Android also serializes integral `Double` values such as `1.0` as `1`
  after the old signer had signed `1.0`. Android now canonicalizes the transmitted
  numeric values and strings/arrays consistently with the broker. Python vectors
  and actual Android-framework JSON tests cover both defects, fractional costs,
  exponent notation and Unicode. No key, signature domain, broker verifier,
  expiry, replay or Guardian-generation check is weakened.

The historical live HTTP 403 cause is **unconfirmed**. The isolated HTTP contract
test reproduces `development_approval_signature` rejection for both old encodings
and acceptance for corrected encoding. It uses real ECDSA verification, the
gateway, operator dispatcher and admission broker, with disposable keys/ledgers
and IPC framing doubles; it is not proof of live socket or Samsung acceptance.
The original operator/admission audit event and request correlation are still
needed to identify the historical cause. No live approval was attempted.

## Review interface

The existing authenticated evidenced-improvement endpoint additionally requires
`review.source_sha`, `review.evidence_sha256`, a concrete `review.reproduction`,
and `review.file_findings` for exactly the proposed paths. These are an explicit
trusted reviewer attestation, not machine proof that arbitrary text is true.
The API supplies the audit actor after its existing admin-token check; task data
cannot select an authentication verifier. Signed owner execution approval and
separate Guardian activation remain mandatory downstream.

`reevaluate_candidate_id` explicitly requests a new source-bound candidate from
historical evidence; normal preparation cannot refresh an existing binding.
Old scoped candidates without this review are safely classified as requiring
review, not automatically admitted or rewritten.

For an authorized disposable review snapshot, the read-only inventory command is
`python -m tools.review_improvement_candidates --database SNAPSHOT --source-root CLEAN_SOURCE`.
It emits IDs, classifications and source hashes, not conversation/tool contents.
It does not initialize or migrate the database.

## Local verification

- Complete Python suite (`bridge/tests tools/tests developer_gateway/tests`):
  **2,578 passed**, 143 subtests passed, three existing opt-in real-Docker
  deployment/rollback tests skipped; one upstream Starlette deprecation warning.
- Android: **315 phone, 30 protocol and 1 Wear tests passed**; Phone and Wear lint
  passed. Includes Robolectric tests using actual framework JSON. No APK was
  production-signed, installed or published.
- Installer regression suite: **32 passed**, including exact historical payload
  reconstruction, migration, activation-failure rollback, modified/unknown bundle
  rejection and preservation of fixture databases, policy, manifests and state.
- Ruff correctness/formatting, CI-scoped Mypy, compilation and high-severity Bandit
  passed. Dependency auditing found no known vulnerabilities. Unified product
  baseline validation passed; historical Android release-preservation tests are
  included above.
- Pinned Gitleaks
  `sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f`:
  clean tracked/new-source export passed, no leaks.

The first working-directory scan reported three findings in ignored `.pyc`
files: two in connector redaction code and one in its security regression test.
Inspection of their compiled constants established that they are the same PEM
delimiter strings and deliberately invalid `very-secret` fixture as the existing
source; none parses as a private key. Source-level `gitleaks:allow` comments do
not survive compilation. No suppression or scanning policy was added or changed;
no real secret was found or committed. Cache files are excluded from Git already.

Initial full-suite environment failures were resolved without weakening tests:
the original worktree had group-writable checkout files, `/tmp` had an unrelated
`.git` directory, and a long disposable directory exceeded Unix socket limits.
The successful run used a disposable normal-permission checkout and short
`/var/tmp` test paths. The original worktree and unrelated temporary files were
preserved. Tests use the existing isolated Python virtual environment, not
system-wide packages.

## Protected payload and live state

Exact installed/source bundle:
`2d0da2c88fabcfe81d2dad65954888fd5e9831830f792dc462c73a22e7e03701`.

Proposed destination, recalculated from the payload:
`890ded5e01f331470355e9044fc3558dffa02ebfb381fc7247ba41edf5290f2d`.

Only the Governor runtime changes within the protected payload. The existing
version-bound installer and rollback mechanism are retained. Installation
requires a separate reviewed, immutable-source procedure and owner approval;
this draft does not install anything.

Read-only verification during this repair found Core healthy at the base SHA,
the clean canonical worktree and remote at that SHA, and Guardian STOPPED/trusted
at generation `b01a6dce-46d3-4068-8e9e-29e411b4470d`. The running Developer Gateway
reports `58cb3afdfe2ed56caa8ed451344efb7ffe005090`, not the current canonical SHA.
That is a live-version reconciliation item, not repaired by changing source here.
No production database, installed broker, credential, Android installation,
service configuration or Guardian state was modified.
