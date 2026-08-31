# Jarvis architecture

Jarvis is one product with one authoritative Brain/Core and multiple trusted
endpoints. `jarvis/unified-production` is the sole long-lived source branch.
Phone, Watch, Home Assistant, integrations, and Developer tooling do not run
independent Jarvis brains.

## System overview

```text
Phone / Watch / Home Assistant / approved service input
                         │
                         ▼
             authentication + principal
                         │
                         ▼
             conversation + turn lifecycle
                         │
                         ▼
                 Jarvis Brain / Core
                         │
              planner + capability registry
                         │
        ┌────────────────┼───────────────────┐
        ▼                ▼                   ▼
   memory/jobs      tools/providers     Home Assistant
        │                │                   │
        └──────── execute + verify ──────────┘
                         │
            action receipt + state update
                         │
                         ▼
        response/notification to the correct endpoint
```

## Phone and Watch endpoints

The Android Phone application owns the local chat and voice interface,
assistant role, compact overlay, wake word, diagnostics, Integrations UI,
Developer UI, endpoint selection, and OTA user experience. It keeps a local
history/cache for presentation and recovery, while Core remains authoritative
for principal-scoped conversation truth and Brain state.

The Wear OS application provides its activity, Tile, assistant entry point,
microphone/audio endpoint, and Phone/Watch handover. Phone and Watch share the
`wearprotocol` Gradle module so message types and routing cannot drift
independently. Both use the same Core conversation identifiers and identity
model.

## Authentication and identity

Requests resolve to a principal before entering a conversation. The common
identity vocabulary includes `principal_id`, `conversation_id`, `turn_id`,
`device_id`, `endpoint`, `capability_id`, `action_id`, `job_id`, and
`receipt_id`. Mobile HTTP and realtime connections use the configured mobile
credential; integration accounts and durable work are principal scoped.

Clients must not invent user identity, provider state, capability availability,
or external action results.

## Conversations, Brain, and memory

Jarvis Core owns the conversation engine, dialogue policy, model routing,
context construction, tool outcomes, and response generation. Conversations
and messages are durable, enabling endpoint continuity and restart recovery.

The Brain-level memory architecture combines current context with persistent
memory, person/house context, event history, preferences, and verified action
history. Separate SQLite stores remain where they have distinct responsibilities,
but they are accessed through the unified Core and share principal/conversation
semantics.

## Planner, capabilities, and verification

The planner resolves intent against one capability registry. A capability
reports a truthful state such as Available, Setup Required, Degraded, or
Unavailable. The model cannot promote generated text into proof that a tool or
external action ran.

Immediate writes follow this lifecycle:

```text
resolve → validate capability → authorize → execute once → verify → receipt → respond
```

Future work follows this lifecycle:

```text
resolve → validate → persist job → verify persistence → promise
        → execute later → verify → notify once → complete idempotently
```

## Realtime runtime

Core provides the single realtime WebSocket runtime for Phone and Watch.
Protocol version `2` covers authentication, endpoint kind, conversation sync,
client turn IDs, streaming, application ping/pong, interruption, and terminal
turn state. The persistent turn ledger provides duplicate admission protection,
delivery fencing, provider-generation checks, response recovery, and restart
continuity.

Standard and Live voice modes use the same Brain and conversation lifecycle.
Audio ownership and routing determine the active Phone or Watch endpoint
without creating a second conversation system.

## Home Assistant, presence, and vision

Home Assistant is a capability, not a competing conversation brain. It remains
authoritative for entities, devices, areas, automations, cameras, and current
state. Jarvis performs grounded entity resolution, inspects fresh state,
enforces approval policy, executes controlled operations, verifies results,
and persists action receipts.

Presence, house awareness, cameras, Frigate, and vision observations feed the
same Brain context and proactive pipeline. Jarvis must not invent entities,
people, locations, or camera evidence.

## External Agent, Web, and integrations

The External Agent Platform supplies connector metadata, provider health,
planning, provenance, and verified results. Web and research capabilities
enforce URL policy, private-address and metadata protections, DNS-rebinding
defenses, and source provenance.

Integrations & Accounts stores encrypted, principal-scoped account state.
Google OAuth uses Authorization Code with PKCE, one-time state, callback replay
protection, exact redirect validation, refresh/expiry handling, revocation,
and provider health checks. Gmail, Calendar, and Contacts are available only
when configured and healthy; otherwise they truthfully report Setup Required
or Unavailable.

## Durable and proactive work

One durable jobs/follow-up system owns reminders, schedules, monitoring,
conditional work, email/reply monitoring, retries, cancellation, and
idempotent completion. It persists before Jarvis promises future work and
survives Core restarts.

Natural-language reminders are normalized into exact timezone-aware timestamps;
recurrence is persisted as a structured schedule. Explicit personal memory is
handled by the same principal-scoped memory engine. Task status and management
use authenticated Core APIs. See [Personal Assistant v1](docs/PERSONAL_ASSISTANT_V1.md)
for supported commands, lifecycle guarantees, and current limitations.

Home events, monitoring results, and scheduled work enter the same proactive
engine and notification path. Results route to the originating principal and
conversation; mobile notification is supplemental delivery rather than a
replacement for durable conversation state.

## Developer gateway and self-improvement

The separate Developer/Codex gateway is authenticated, audited, rate-limited,
and deployed from the same authoritative source revision as Core. It exposes
controlled development actions without giving ordinary model output deployment
authority.

Self-improvement records mistakes and manages candidate generation, inspection,
prepare, approval, deploy, reject, validation, and rollback states. Production
changes remain approval gated and use the unified repository and deployment
provenance.

## Persistence and deployment

Production state is outside Git and outside container layers. Persistent mounts
hold configuration, SQLite databases, conversations, memory, durable jobs,
receipts, credentials, logs, and speaker identity. Deployment verifies database
integrity before and after container replacement and never recreates those
mounts as part of a normal source update.

Core health reports the exact source SHA. The Developer gateway reports the
same provenance and authoritative workspace. Active services must not import
from historical worktrees or retired branch paths.

## Release lineage

Core deployment and Phone/Watch release tooling accept only
`jarvis/unified-production` or an approved immutable `v*` tag at its current
head. A product release builds Phone and Watch from one commit, verifies the
production signer and package identity, inspects the APKs, and records matching
Core/Phone/Watch source SHAs in the product and OTA manifests. Channel metadata
is hosted as validated GitHub Release assets; no development branch stores
mutable OTA state.

The current published product is `v19.0.0-alpha25`, Core application version
`3.7.0`, realtime protocol `2`.
