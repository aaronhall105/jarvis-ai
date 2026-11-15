# Manual owner review: no 90-second task countdown

## Behaviour

New production tasks created under `JARVIS_SUPERVISED_REVIEW_MODE=manual`
use development-approval protocol version 2. They remain in
`APPROVAL_REQUESTED` without a wall-clock approval deadline. Explicit signed
owner approval and a separate task-bound Guardian activation are still required.
The phone displays **Review deadline: none**. Its execution-time limit is labelled
**execution only** so it cannot be mistaken for a human-review countdown.

Version 2 encodes this policy as the exact pair `version: 2, expires_at: 0`.
It has a separate ECDSA signature domain:
`jarvis-development-admission-v2\n`.
Missing expiry, `false`, `0.0`, negative expiry, unknown protocol versions and
positive version-2 expiry are not interpreted as permission to wait indefinitely.
No distant date is used to simulate a disabled countdown.

## Controls retained

The same enrolled owner key, exact task ID, approval ID, source SHA, parameter
hash, file allow-list, provider/model, maximum calls and cost, and Guardian
generation remain binding. An accepted signature alone does not activate the
worker. Source drift, an unrelated Guardian cycle, untrusted state, missing
signature, revoked key, altered task parameters and ambiguous execution continue
to prevent admission through the existing gates.

Execution remains subject to the signed `deadline_seconds` limit (at most
120 seconds), the provider-call cap, cost reservation, worker watchdog, STOP
fencing and no-automatic-retry rules. Removing the human-review deadline does
not remove any execution limit.

Preparation and activation messages retain their existing short replay/freshness
windows. A stale **read-only preview** may need refreshing; it does not expire the
version-2 review task or turn it into a new candidate. The later PR-publication
approval remains a separate, bounded operation.

The existing controller service is already `Type=simple` with
`TimeoutStartSec=infinity`. While waiting it continues its existing observation
loop, without provider execution. Its memory, CPU, task-count, stop and
worker-execution limits are unchanged. The persisted protocol version is
reconstructed on restart; restarting does not convert a v1 approval to v2.

## Historical records

Version-1 requests retain their old expiry semantics. In particular, already
failed candidates 78 and 80 are not revived, timestamps are not edited, old
signatures are not rewritten, and claimed candidate IDs are not reused.
The configuration applies to newly created tasks, not to old task records.

## Rollout

1. Run the Python and Android regression suites and the exact-head protected CI
   and CodeQL gates; review the complete change before merging.
2. Build and install the corresponding Android APK with the existing production
   signing identity, in place without clearing data. An older phone rejects v2
   instead of silently authorizing it.
3. Upgrade the protected broker, its unit, Gateway and aligned source through the
   existing reviewed-source/integrity-preserving deployment process. Do not edit
   installed protected Python files or rewrite integrity hashes by hand.
4. The reviewed controller unit sets
   `Environment=JARVIS_SUPERVISED_REVIEW_MODE=manual`. Ensure no later environment
   file overrides it to `timed`. Check the installed unit and broker source match
   the reviewed build while Guardian is STOPPED/trusted.
5. Scope the fresh, unclaimed request against the newly deployed canonical SHA.
   Candidate 81 should not be prepared against an obsolete source binding.
6. After signed preparation, verify the published task has `version=2` and
   `expires_at=0`, its UI says **Review deadline: none**, and no provider call has
   occurred. Approve and activate only that exact task when ready.

Preserve the existing operator/publication home-isolation overrides, persistent
SQLite WAL helpers, database records, credentials and enrolled phone key during
rollout. No new signing workflow or deployment is triggered by the patch builder.
