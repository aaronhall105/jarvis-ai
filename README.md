# Jarvis AI

[![Jarvis CI](https://github.com/aaronhall105/jarvis-ai/actions/workflows/jarvis-ci.yml/badge.svg?branch=jarvis%2Funified-production)](https://github.com/aaronhall105/jarvis-ai/actions/workflows/jarvis-ci.yml)
[![Android OTA release](https://github.com/aaronhall105/jarvis-ai/actions/workflows/android-ota-release.yml/badge.svg)](https://github.com/aaronhall105/jarvis-ai/actions/workflows/android-ota-release.yml)
[![Release](https://img.shields.io/badge/release-v19.0.0--alpha13-orange)](https://github.com/aaronhall105/jarvis-ai/releases/tag/v19.0.0-alpha13)

A self-hosted realtime AI assistant for Android and Home Assistant.

Jarvis combines an Android voice-and-chat client, a Docker-hosted AI Core and
Home Assistant tooling. The Core remains authoritative for conversation,
memory, model access and smart-home actions.

> **Status:** Alpha prerelease. The current tested release is
> `v19.0.0-alpha13`.

## Highlights

- Typed and spoken conversations with streamed chat responses.
- Android default-assistant support and a compact assistant overlay.
- Offline wake phrase detection with bounded recovery and background re-arming.
- Adaptive reply lengths so commands remain concise while detailed answers and
  stories can complete normally.
- Early speech from the first useful sentence, with bounded speech for long
  replies.
- Home Assistant entity discovery and controlled smart-home actions.
- Persistent conversation context, room context and household awareness.
- Proactive alerts, Core-first vision support and diagnostics.
- Isolated testing, validation and rollback controls for releases.

## Architecture

```text
Android client
    │  WebSocket / HTTP
    ▼
Jarvis Core
    ├── conversation and memory
    ├── model orchestration
    ├── Home Assistant tools
    ├── proactive intelligence
    └── vision and room context
             │
             ▼
       Home Assistant
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the component boundaries and
deployment model.

## Repository layout

| Path | Purpose |
| --- | --- |
| `bridge/` | Jarvis Core service, policies, tools and tests |
| `android/jarvis-voice-client/` | Android assistant, chat UI and wake-word client |
| `home_assistant/` | Home Assistant conversation integration and tests |
| `config/` | Runtime configuration mounted into Jarvis Core |
| `data/` | Persistent local databases and memory; excluded from Git |
| `logs/` | Runtime logs; excluded from Git |
| `tools/` | Installation, validation and maintenance utilities |
| `docs/` | Current documentation and archived release material |
| `.github/workflows/` | CI, security and Android build workflows |

## Quick start

```bash
git clone \
  --branch jarvis/unified-production \
  https://github.com/aaronhall105/jarvis-ai.git

cd jarvis-ai
cp .env.example .env
```

Edit `.env` with the required model and Home Assistant settings, then start the
Core:

```bash
docker compose up -d --build
curl -fsS http://localhost:8000/health
```

The service listens on port `8000` and persists `config`, `data` and `logs`
through bind mounts.

Full instructions are in [INSTALL.md](INSTALL.md).

## Android application

The current prerelease APK is attached to the
[`v19.0.0-alpha13` release](https://github.com/aaronhall105/jarvis-ai/releases/tag/v19.0.0-alpha13).

Android source is located in:

```text
android/jarvis-voice-client/
```

The release workflow compiles unit tests and the debug APK using Java 17,
Android SDK 36 and Gradle 9.4.1.

## Documentation

- [Installation](INSTALL.md)
- [Architecture](ARCHITECTURE.md)
- [Changelog](CHANGELOG.md)
- [Current Alpha13 release notes](CHANGES_V19_0_0_ALPHA13.md)
- [Documentation index](docs/README.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

## Security

Never commit `.env`, access tokens, Home Assistant credentials, private keys,
runtime databases or personal household data.

Security reports should follow [SECURITY.md](SECURITY.md).
