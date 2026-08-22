# Jarvis AI architecture

Jarvis is a self-hosted household AI platform split into three product surfaces
with explicit ownership boundaries.

## Android client

The Android application is the conversational client. It owns:

- microphone capture and speech recognition;
- offline wake phrase detection and re-arming;
- default-assistant and overlay entry points;
- typed chat and streamed answer rendering;
- audio playback, interruption and conversation closure;
- connection failover, diagnostics and user-facing settings.

The Android client is not a second Home Assistant dashboard and is not the
authority for long-term memory or smart-home decisions.

## Jarvis Core

Jarvis Core is the authoritative AI orchestration service. It owns:

- conversation orchestration;
- model-provider access;
- persistent conversation and household context;
- memory and response policies;
- Home Assistant tool selection and action execution;
- proactive intelligence and alert policy;
- vision interpretation and room context;
- realtime voice protocol events;
- diagnostics, validation and release identity;
- supervised self-improvement coordination.

The Core runs as the `jarvis-core` Docker service on port `8000`.

## Home Assistant

Home Assistant remains authoritative for:

- entities, devices and areas;
- automations and scripts;
- dashboards and live camera views;
- integrations, energy data and configuration;
- smart-home state and permission checks.

Jarvis requests controlled operations through its Home Assistant integration; it
does not replace Home Assistant's state model.

## Data flow

```text
User voice or text
        │
        ▼
Android client / room voice endpoint
        │
        │ realtime protocol / HTTP
        ▼
Jarvis Core
        ├── conversation and user context
        ├── memory and room context
        ├── model request
        ├── policy and safety checks
        ├── optional vision interpretation
        └── optional Home Assistant tool call
                          │
                          ▼
                   Home Assistant
                          │
                          ▼
                 verified tool result
        │
        ▼
streamed text / voice / proactive outcome
```

## Persistence

Runtime persistence is stored outside the Core container through Compose bind
mounts:

- `config/` for local configuration;
- `data/` for databases, memory and durable state;
- `logs/` for runtime logs.

Additional deployment-specific stores, such as speaker identity data, must also
remain outside source control.

Secrets belong in `.env`, which must never be committed.

## Trust boundaries

- Model providers generate language and reasoning output but are not the
  authority for household state.
- Home Assistant remains the source of truth for devices and entities.
- Voice ID is convenience identity and must not be the sole factor for
  high-impact actions.
- The live Core container does not directly rewrite production source.
- Self-improvement candidates are prepared outside the live container, checked
  against allow-lists and tests, and require explicit human approval.

## Release safety

Release tooling uses validation branches/worktrees and health checks. CI covers
Core regressions, Home Assistant integration checks, Android unit/build checks,
correctness linting, security scanning and dependency auditing.

Deployment-specific release workflows retain rollback controls rather than
treating a successful build as sufficient evidence of production health.

## Current release boundary

The default `conversation-engine` branch currently identifies itself as:

- Jarvis `19.0.0-alpha17`;
- Core application API `3.7.0`;
- realtime protocol `2`.

Alpha19 production-hardening work is currently isolated in draft PR #1 and
should not be described as shipped behaviour until merged.

See [PROJECT_STATUS.md](PROJECT_STATUS.md) for the current boundary.
