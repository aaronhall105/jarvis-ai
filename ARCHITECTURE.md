# Jarvis AI architecture

Jarvis is split into three product surfaces with explicit ownership boundaries.

## Android client

The Android application is the conversational client. It owns:

- Microphone capture and speech recognition.
- Offline wake phrase detection.
- Default-assistant and overlay entry points.
- Typed chat and streamed answer rendering.
- Audio playback, interruption and conversation closure.
- Connection failover, diagnostics and user-facing settings.

The Android client does not become a second Home Assistant dashboard and is not
the authority for memory or smart-home decisions.

## Jarvis Core

Jarvis Core is the authoritative AI service. It owns:

- Conversation orchestration.
- Model-provider access.
- Persistent conversation and household context.
- Memory and response policies.
- Home Assistant tool selection and action execution.
- Proactive intelligence, smart alerts and vision processing.
- Realtime voice protocol events.
- Validation, diagnostics and release identity.

The Core runs as the `jarvis-core` Docker service on port `8000`.

## Home Assistant

Home Assistant remains authoritative for:

- Entities, devices and areas.
- Automations and scripts.
- Dashboards and camera views.
- Integrations, energy data and configuration.
- Permission checks for smart-home actions.

Jarvis requests controlled operations through the Home Assistant integration;
it does not replace Home Assistant's state model.

## Data flow

```text
User voice or text
        │
        ▼
Android client
        │  realtime protocol
        ▼
Jarvis Core
        ├── conversation context
        ├── model request
        ├── policy and safety checks
        └── optional Home Assistant tool call
                          │
                          ▼
                   Home Assistant
                          │
                          ▼
                 verified tool result
        │
        ▼
streamed text and bounded speech
```

## Persistence

Runtime persistence is stored outside the container through Compose bind mounts:

- `config/` for local configuration
- `data/` for databases, memory and durable state
- `logs/` for runtime logs

Secrets belong in `.env`, which must remain outside Git.

## Release safety

Release scripts use isolated worktrees and validation branches. A candidate must
pass source checks, Core regressions and Android compilation before the
production branch is advanced. The deployed Core retains a rollback image until
the new release is confirmed healthy.

## Current release

`v19.0.0-alpha13` adds persistent wake recovery, adaptive response budgets,
earlier bounded speech, a simplified toolbar and reorganised Android settings.
