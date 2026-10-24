# Phase 1 — observe-only development planning

This is local feature work, not an autonomous engineering/release deployment.
Guardian remains STOPPED. No broker, Guardian, production Core or Developer
installation is changed by the observer or its certification probe.

## What is implemented

`developer_gateway.autonomy.Governor` reuses the existing private State locking
and transactional SQLite machinery in a **separate** journal. It refuses source
worktree paths and existing non-observer databases. A second process cannot own
the journal concurrently. Goals preserve their original typed evidence, reason,
priority, timestamps, expected improvement and success/rollback measurements.

The initial evidence adapter reads only fixed localhost Core health, system
status and Developer health endpoints. It retains whitelisted booleans, not
configuration, tokens, transcripts, error bodies or source. Missing observations
are unknown; they are not invented product failures. Stale evidence expires from
capability availability. Voice, communications and research capabilities that
this adapter cannot verify are explicitly shown as **Not yet verified**.

Broken health signals can create deduplicated diagnostic goals. Core/storage
issues take priority over an individual integration. Recovered health moves an
idea to WATCHING, not COMPLETED: recovery does not prove a deployed improvement.
Unchanged rejected ideas do not reopen. This initial adapter does not yet ingest
user corrections, latency distributions, memory misses or capability-demand data.

Research reuses the existing ResearchEngine interface. A claimed question is
durable before execution; failed/interrupted questions do not retry on every
observation. Only bounded HTTPS provenance is retained as hypotheses, never
authority. Research cancellation has an explicit interrupted outcome. There is
no configured live research provider in the observer sidecar yet; healthy idle
observations do not trigger research or model requests. Do not claim live research
success from the deterministic adapter tests.

No method creates a Developer task, worktree, commit, push, PR or deployment.
Even ACTIVE Guardian state yields PAUSED in this Phase 1 implementation.
Later engineering must use the dedicated autonomous authority and existing API
Developer/broker infrastructure, **not** inherit the host operator's privileges.

## Scheduling and API

Startup recovery and six-hour scheduled health reviews are model-free. Explicit
dashboard observations coalesce with startup and each other for 30 seconds.
No tight polling loop or model heartbeat exists.

The gateway integration is disabled by default. Opt-in configuration is
`JARVIS_AUTONOMY_OBSERVER_ENABLED=true`, with a separate private
`JARVIS_AUTONOMY_OBSERVER_STATE` path outside source worktrees. It has **not** been
enabled in the production gateway. Observer startup failure cannot prevent
normal manual Developer startup.

Authenticated routes:

- `GET /api/autonomy`: factual snapshot, no provider response bodies.
- `POST /api/autonomy/observe`: fixed read-only health collection, no caller URLs.
- `POST /api/autonomy/control`: bounded signed operator envelope relay only.

Phase 1 rejects `resume` with HTTP 403 before IPC even for an enrolled signed
request. Android offers STOP only and explains that activation is unavailable.
This is additional to, not a replacement for, broker authorization. Control HTTP
failures are not retried against another endpoint. The relay has no
signing key or enrollment endpoint. Root policy, kernel relay identity, enrolled
P256 device signature, generation, expiry and durable replay checks remain the
installed broker's authority. Empty enrollment remains DENIED. No UI optimistic
toggle is used.

## Android foundations

Home has a Jarvis Development destination. The monochrome screen shows stopped,
idle/planned work, evidence-based service availability, recent improvements,
rollback outcomes and collapsed technical details. Empty lists stay empty.
Unmerged local WIP is never shown as a production improvement.

STOP uses an Android Keystore-held key and the signed broker protocol. Shared
contract tests cover RESUME cryptography, but Phase 1 does not expose activation.
The public-only export and local operator ceremony are documented in
[autonomy-operator-enrollment.md](autonomy-operator-enrollment.md). They do not
enroll a device automatically or activate autonomy. Live enrollment is outstanding.
Generating a device key grants no authority: public-key enrollment must occur
through a separately reviewed operator path. That enrollment has not occurred.
The signed control transport is implemented/tested, but real phone controls are
not certified or available merely because the screen compiles.

No APK was installed, no app data/authentication was cleared, and no OTA/release
metadata was published. Alpha39 remains public; the prepared alpha40/190420
candidate follows synchronized Phone/Wear identities and the +10 version-code
sequence. See the signing review for explicit release/install approval boundaries.

## Reproducible read-only sidecar certification

From the feature worktree:

```sh
PYTHONPATH=. /home/aaron/.venvs/jarvis-phase2/bin/python tools/certify_autonomy_observer.py
```

This requires trusted STOPPED Guardian and clean canonical production. It starts
two successive localhost-only server processes with one temporary private
journal and an in-memory random authentication token. It checks authentication,
reconnect, restart, identical roadmap identities, zero Developer threads/tool
calls, and unchanged canonical worktree. Both sidecars terminate afterwards.
Only content-free counts and the journal directory are printed. No production
credential is read or copied. This is not physical PHONE/WATCH certification.

Root-free broker regressions and the installed admission-module probe prove
STOPPED denies registration, verification, build and release before any execution
callback; unsupported raw source/Git operations are denied separately. The
installed-module probe uses actual Guardian status and an unprivileged temporary
ledger. It is not a claim to have connected as UID995 to the worker socket.

## Boundary before Phase 2

Do not launch engineering, install services, or enable autonomy from the observer.
The existing operator command is `jarvis-autonomy resume`; only Aaron may run it
when moving to controlled Phase 2 certification. The observer does not execute
that command. It cannot itself become the later autonomous engineering worker.
Local STOP remains `jarvis-autonomy stop`, independent of the observer and API.
