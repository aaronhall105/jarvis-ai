# Changelog

## Current prerelease

### v19.0.0-alpha39 — room occupancy intelligence

Alpha39 adds one deterministic, durable room occupancy state machine shared by
conversation, HomeExperience, Android, and Home Assistant. Strong, supporting,
and diagnostic evidence retain uncertainty; detector-off never means empty,
`UNKNOWN` never authorizes lights-off, and persisted state is reconciled with
live Home Assistant evidence after restart.

See [the complete alpha39 release notes](docs/releases/CHANGES_V19_0_0_ALPHA39.md).

Core application version remains `3.7.0`; realtime protocol remains `2`.

### v19.0.0-alpha38 — home experience canonicalization and UX polish

Alpha38 canonicalizes grounded HomeExperience presentation around physical
items, keeping alternate streams, raw entities, and detailed evidence under
Diagnostics. Android now presents compact rooms, cameras, energy, activity,
offline status, and detail navigation without weakening verified action
targets or alpha37 freshness and cache semantics.

See [the complete alpha38 release notes](docs/releases/CHANGES_V19_0_0_ALPHA38.md).

Core application version remains `3.7.0`; realtime protocol remains `2`.

### v19.0.0-alpha37 — unified home experience

Alpha37 projects grounded Home Assistant state, physical-device roll-ups, and
durable proactive evidence into one authenticated `HomeExperience` contract.
Android, conversation, and a Home Assistant dashboard bridge now present the
same people, rooms, lights, availability, incidents, activity, and freshness
semantics. Explicit light actions retain exact-set verification, while cached
mobile state is visibly offline and read-only.

See [the complete alpha37 release notes](docs/releases/CHANGES_V19_0_0_ALPHA37.md).

Core application version remains `3.7.0`; realtime protocol remains `2`.

### v19.0.0-alpha36 — proactive home intelligence

Alpha36 rolls Home Assistant entity failures up to grounded physical devices,
selects document evidence from the relevant attachment-bearing message rather
than blindly preferring the newest reply, and adds a durable proactive pipeline
with persistence filtering, deduplication, recovery, quiet-hours policy,
delivery outcomes, and evidence-backed follow-up explanations.

See [the complete alpha36 release notes](docs/releases/CHANGES_V19_0_0_ALPHA36.md).

Core application version remains `3.7.0`; realtime protocol remains `2`.

### v19.0.0-alpha35 — whole-home intelligence and grounded measurements

Alpha35 adds model-interpreted, deterministically grounded whole-home and room
queries that preserve zero/one/many entity sets, durable conversational
references, safe exact-set actions, and a reusable HomeSnapshot. It also keeps
evidence-backed currency and measurement units attached through document
extraction, WorkingContext, comparisons, and natural result presentation.

See [the complete alpha35 release notes](docs/releases/CHANGES_V19_0_0_ALPHA35.md).

Core application version remains `3.7.0`; realtime protocol remains `2`.

### v19.0.0-alpha34 — premium shared shell and persistent chat input

Alpha34 gives Chat and Tasks one stable visual shell, a lighter Smart Inbox
presentation, and a shared token-based Android design language. Typed and IME
sends now clear the composer while retaining focus and the open keyboard;
streaming updates no longer steal input focus.

See [the complete alpha34 release notes](docs/releases/CHANGES_V19_0_0_ALPHA34.md).

Core application version remains `3.7.0`; realtime protocol remains `2`.

### v19.0.0-alpha33 — truthful continuous task monitoring

Alpha33 distinguishes finite task backlogs from continuous monitoring. Once a
bounded phase completes, Task Centre retains its historical outcome but no
longer presents an estimated denominator as unfinished work, a completion
percentage, remaining count, or ETA. Android separately reports successful API
sync freshness, preserves offline snapshots, and uses a responsive four-part
filter row.

See [the complete alpha33 release notes](docs/releases/CHANGES_V19_0_0_ALPHA33.md).

Core application version remains `3.7.0`; realtime protocol remains `2`.

### v19.0.0-alpha32 — resilient Core connectivity and task progress

Alpha32 gives every Core-backed Android feature one endpoint authority with
configured LAN/remote failover and an authenticated last-known-good route. The
Tasks screen keeps its last redacted snapshot while offline and adds structured
percent, remaining work, evidence-based ETA, and provider progress without
parsing presentation prose.

See [the complete alpha32 release notes](docs/releases/CHANGES_V19_0_0_ALPHA32.md).

Core application version remains `3.7.0`; realtime protocol remains `2`.

### v19.0.0-alpha31 — Unified Task Centre

Alpha31 adds a first-class Android Tasks experience and one principal-scoped,
provider-neutral projection over Jarvis's existing durable work engines. It
includes evidence-backed task details, safe generic controls, truthful waiting
states, and restart-safe completion/failure notification subscriptions.

See [the complete alpha31 release notes](docs/releases/CHANGES_V19_0_0_ALPHA31.md).

Core application version remains `3.7.0`; realtime protocol remains `2`.

### v19.0.0-alpha30 — OTA recovery and safe multi-inbox cleanup

Alpha30 contains the approved compound Gmail and Outlook cleanup work prepared
for alpha29, plus a release-pipeline repair that avoids requesting the removed
legacy Android SDK `tools` package. Alpha29's immutable tag was consumed by the
failed setup run and did not publish APKs or update the OTA channel.

See [the complete alpha30 release notes](docs/releases/CHANGES_V19_0_0_ALPHA30.md).

Core application version remains `3.7.0`; realtime protocol remains `2`.

## Historical releases

### v19.0.0-alpha29 — Safe multi-inbox cleanup

Alpha29 adds compound Gmail and Outlook cleanup requests with durable provider
scope, exact frozen candidate sets, explicit confirmation, recoverable-only
destinations, truthful partial-provider results, and structured handled-action
outcomes for Android. It preserves Email Assistant v3, Smart Important Only,
and the optional Astra Executive Agent architecture already deployed in Core.

See [the complete alpha29 release notes](docs/releases/CHANGES_V19_0_0_ALPHA29.md).

Core application version remains `3.7.0`; realtime protocol remains `2`.

### v19.0.0-alpha28 — Email Assistant v3 and Outlook

Alpha28 packages the already-merged Email Assistant v3 and its unified Gmail
and Outlook/Microsoft 365 provider architecture. It adds Microsoft Graph and
the Android Outlook connection controls, durable dialogue continuations,
bounded frozen-set bulk cleanup, provider-specific Bin/Deleted Items language,
and resilient Gmail incremental-history handling.

Outlook support is included, but it remains **Setup Required** until Microsoft
application configuration and Aaron's browser consent are complete. See
[the complete alpha28 release notes](docs/releases/CHANGES_V19_0_0_ALPHA28.md).

Core application version remains `3.7.0`; realtime protocol remains `2`.

### v19.0.0-alpha27 — Google Personal Integrations v1

Alpha27 packages the live-verified Google Personal Integrations v1 runtime with
Core, Developer gateway, Android Phone, Wear OS, product manifest, and OTA
metadata from one approved `jarvis/unified-production` revision. It connects
Google OAuth, Gmail, Calendar, Contacts, durable Gmail monitoring, verified
action receipts, and Personal Assistant same-conversation delivery through the
existing unified capability architecture. It also fixes Android connected
provider cards rendering a literal `null` detail.

See [the complete alpha27 release notes](docs/releases/CHANGES_V19_0_0_ALPHA27.md)
and the [published GitHub prerelease](https://github.com/aaronhall105/jarvis-ai/releases/tag/v19.0.0-alpha27).

Core application version remains `3.7.0`; realtime protocol remains `2`.

Recent prerelease notes are indexed in
[`docs/releases/README.md`](docs/releases/README.md). Older version-specific
release notes remain under [`docs/releases/archive/`](docs/releases/archive/)
for traceability and are not current installation instructions.

Alpha26 remains available as
[historical release documentation](docs/releases/CHANGES_V19_0_0_ALPHA26.md).

The former root alpha13 note is preserved as
[historical alpha13 release documentation](docs/releases/archive/CHANGES_V19_0_0_ALPHA13.md).
