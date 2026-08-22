# Jarvis AI

[![Jarvis CI](https://github.com/aaronhall105/jarvis-ai/actions/workflows/jarvis-ci.yml/badge.svg?branch=conversation-engine)](https://github.com/aaronhall105/jarvis-ai/actions/workflows/jarvis-ci.yml)
[![Android OTA](https://github.com/aaronhall105/jarvis-ai/actions/workflows/android-ota-release.yml/badge.svg)](https://github.com/aaronhall105/jarvis-ai/actions/workflows/android-ota-release.yml)
[![Status](https://img.shields.io/badge/status-alpha-orange)](PROJECT_STATUS.md)
[![Default%20branch](https://img.shields.io/badge/default-v19.0.0--alpha17-blue)](bridge/app/version.py)

**A self-hosted household AI platform for Android and Home Assistant.**

Jarvis is designed as a persistent personal and household assistant rather than
a simple chatbot wrapper. It combines an Android system-assistant client, a
Docker-hosted AI Core, Home Assistant tools, persistent context, proactive
intelligence, vision support and supervised self-improvement.

> **Current default-branch identity:** `v19.0.0-alpha17`
> **Core application API:** `3.7.0`
> **Realtime protocol:** `2`
> **Development status:** Alpha prerelease. Alpha19 production-hardening work is
> currently tracked separately in [PR #1](https://github.com/aaronhall105/jarvis-ai/pull/1).

See [PROJECT_STATUS.md](PROJECT_STATUS.md) for the shipped-versus-development
boundary.

## What Jarvis is

Jarvis is an orchestration platform around AI models and smart-home systems.

- **Android** is the personal interaction layer.
- **Jarvis Core** owns conversation, memory, model access, policy and tool
  orchestration.
- **Home Assistant** remains authoritative for entities, devices, rooms,
  automations, cameras and integrations.
- **Model providers** supply reasoning and generation; they are not embedded as
  the authority for household state.

The goal is one assistant that can keep context across chat, voice, rooms,
people and household events while preserving explicit control boundaries.

## Architecture

```text
                    ┌───────────────────────────┐
                    │          Person           │
                    └─────────────┬─────────────┘
                                  │ voice / text
                                  ▼
                    ┌───────────────────────────┐
                    │      Android client       │
                    │ assistant • overlay • UI  │
                    │ wake • capture • playback │
                    └─────────────┬─────────────┘
                                  │ WebSocket / HTTP
                                  ▼
┌─────────────────────────────────────────────────────────────────┐
│                         Jarvis Core                             │
│ conversation • memory • model orchestration • policy • tools   │
│ awareness • proactive intelligence • vision • diagnostics      │
└──────────────────┬──────────────────────────────┬───────────────┘
                   │ controlled tool calls        │ optional AI APIs
                   ▼                              ▼
        ┌──────────────────────┐          ┌──────────────────────┐
        │    Home Assistant    │          │   Model / TTS APIs   │
        │ devices • areas      │          │ reasoning • voice    │
        │ automations • state  │          └──────────────────────┘
        └──────────────────────┘
```

Detailed ownership boundaries are documented in
[ARCHITECTURE.md](ARCHITECTURE.md).

## Major capabilities

| Area | Current repository capability |
| --- | --- |
| Conversation | Typed and spoken conversations with streamed responses and persistent conversation state |
| Android assistant | Default-assistant integration, compact overlay, full chat UI, settings and diagnostics |
| Wake and voice | Offline wake detection, background re-arming, realtime voice transport, interruption and echo handling |
| Smart home | Home Assistant entity/area discovery and controlled action execution |
| Memory and context | Persistent conversation memory, user context, room context and household awareness |
| Proactive intelligence | Event-driven alerts, quiet-hours policy, acknowledgement, snooze, suppression and escalation |
| Vision | Frigate/Home Assistant camera context and Core-side vision interpretation |
| Voice ID | Multi-user speaker recognition using stored speaker embeddings with Guest fallback for uncertainty |
| Self-improvement | Supervised failure/correction pipeline with isolated worktrees, tests, approval gates and rollback |
| Release engineering | Docker health checks, CI, Android builds, security scans and OTA release tooling |

Some capabilities remain experimental or deployment-specific. Jarvis is not yet
a finished Alexa/Google-style consumer product.

## Repository layout

| Path | Purpose |
| --- | --- |
| `bridge/` | Jarvis Core service, policies, engines, tools and tests |
| `android/jarvis-voice-client/` | Android assistant, chat UI, wake and realtime voice client |
| `home_assistant/` | Home Assistant conversation integration and tests |
| `config/` | Runtime configuration mounted into Jarvis Core |
| `data/` | Persistent local databases and memory; excluded from Git |
| `logs/` | Runtime logs; excluded from Git |
| `tools/` | Installation, validation, release and maintenance utilities |
| `systemd/` | Host-side services used by supervised workers |
| `docs/` | Current documentation plus archived release material |
| `.github/workflows/` | CI, CodeQL, Android build and OTA workflows |

## Quick start

```bash
git clone \
  --branch conversation-engine \
  https://github.com/aaronhall105/jarvis-ai.git

cd jarvis-ai
cp .env.example .env
chmod 600 .env
```

Configure the required model and Home Assistant settings in `.env`, then start
the Core:

```bash
docker compose up -d --build
curl -fsS http://localhost:8000/health
```

Jarvis Core listens on port `8000` by default and persists `config`, `data` and
`logs` through bind mounts.

Full deployment guidance is in [INSTALL.md](INSTALL.md).

## Android application

Android source lives in:

```text
android/jarvis-voice-client/
```

Build and OTA workflows are maintained under `.github/workflows/`. Published
packages should be obtained from
[GitHub Releases](https://github.com/aaronhall105/jarvis-ai/releases) and checked
against the release identity reported by the application and
`bridge/app/version.py`.

Do not use an old hard-coded APK link as an indicator of the current source
branch.

## Documentation

- [Project status](PROJECT_STATUS.md)
- [Installation](INSTALL.md)
- [Architecture](ARCHITECTURE.md)
- [Validation and quality gates](TESTED.md)
- [Changelog](CHANGELOG.md)
- [Documentation index](docs/README.md)
- [Voice ID](docs/VOICE_ID.md)
- [Proactive action orchestrator](docs/PROACTIVE_ORCHESTRATOR.md)
- [Self-improvement engine](docs/SELF_IMPROVEMENT.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

## Security

Never commit `.env`, API keys, access tokens, Home Assistant credentials,
private keys, runtime databases, speaker databases, captured audio or personal
household data.

Voice recognition is convenience identity, not a strong authentication factor
for high-impact actions.

Security reports should follow [SECURITY.md](SECURITY.md).

## Project maturity

Jarvis is an actively developed alpha system. Features may change between
prereleases, deployment-specific integrations require local configuration, and
production claims should be based on the current CI/test evidence rather than
historical release notes.
