# Jarvis AI

[![Jarvis CI](https://github.com/aaronhall105/jarvis-ai/actions/workflows/jarvis-ci.yml/badge.svg?branch=jarvis%2Funified-production)](https://github.com/aaronhall105/jarvis-ai/actions/workflows/jarvis-ci.yml)
[![CodeQL](https://github.com/aaronhall105/jarvis-ai/actions/workflows/codeql.yml/badge.svg?branch=jarvis%2Funified-production)](https://github.com/aaronhall105/jarvis-ai/actions/workflows/codeql.yml)
[![Android OTA release](https://github.com/aaronhall105/jarvis-ai/actions/workflows/android-ota-release.yml/badge.svg)](https://github.com/aaronhall105/jarvis-ai/actions/workflows/android-ota-release.yml)
[![Release](https://img.shields.io/badge/release-v19.0.0--alpha25-orange)](https://github.com/aaronhall105/jarvis-ai/releases/tag/v19.0.0-alpha25)

Jarvis is a self-hosted unified AI assistant with one authoritative Brain/Core,
an Android Phone client, a Wear OS client, Home Assistant capabilities,
realtime voice, durable work, integrations, and controlled developer tooling.

> **Current product:** `v19.0.0-alpha25` on the sole long-lived and default
> branch, `jarvis/unified-production`. Core application version is `3.7.0`
> and realtime protocol version is `2`.

## Product capabilities

- One Brain/Core owns identity, conversations, memory, planning, capability
  resolution, verification, receipts, and responses.
- Android Phone and Wear OS clients use the same authenticated realtime and
  conversation architecture through the shared `wearprotocol` module.
- Persistent conversations, long-term memory, durable follow-ups, monitoring,
  recurring work, and restart recovery.
- Realtime text and voice with turn IDs, delivery fencing, interruption,
  barge-in, recovery, endpoint routing, and Phone/Watch continuity.
- Android wake word, default-assistant role, compact overlay, chat history,
  Developer mode, Integrations, diagnostics, and in-place OTA updates.
- Grounded Home Assistant reads, presence evidence, verified writes, action
  receipts, proactive household intelligence, vision, cameras, and Frigate.
- External Agent connectors, Web/research provenance, SSRF protections,
  capability health, and approval-aware action execution.
- Principal-scoped Integrations & Accounts with encrypted credentials and
  Google, Gmail, Calendar, and Contacts support when configured.
- An authenticated Developer/Codex gateway and approval-gated self-improvement
  lifecycle.

Google and Microsoft integrations report **Setup Required** until their
credentials are configured and provider health is verified. Setup state is not
reported as a connected account.

## Current architecture

```text
Phone ───────────────┐
                    ├── authenticated HTTP / shared realtime protocol
Wear OS ─────────────┘                         │
                                              ▼
                                   Jarvis Brain / Core
                         ┌────────────────────┼────────────────────┐
                         │                    │                    │
              Conversation + Memory     Planner + Registry   Durable Work
                         │                    │                    │
                         └────────── verification + receipts ──────┘
                                              │
                     ┌────────────────────────┼───────────────────────┐
                     ▼                        ▼                       ▼
              Home Assistant        External Agent / Web      Integrations
             presence + vision       research + providers     Google + mail
                                              │
                                              ▼
                            response to the originating endpoint
```

Phone and Watch are clients of the same Brain; they do not maintain independent
intelligence, identity, or server-side conversation truth. See
[ARCHITECTURE.md](ARCHITECTURE.md) for component ownership and data flow.

## Repository layout

| Path | Purpose |
| --- | --- |
| `bridge/` | Jarvis Brain/Core APIs, policies, capabilities, realtime, persistence, and tests |
| `android/jarvis-voice-client/app/` | Android Phone chat, voice, assistant, Integrations, Developer, and OTA client |
| `android/jarvis-voice-client/wear/` | Wear OS activity, Tile, assistant, voice, and channel endpoint |
| `android/jarvis-voice-client/wearprotocol/` | Shared Phone/Watch wire protocol |
| `developer_gateway/` | Authenticated and audited Developer/Codex gateway |
| `home_assistant/` | Home Assistant conversation integration and tests |
| `tools/` | Validation, release, deployment, recovery, and maintenance utilities |
| `docs/` | Current product documentation and clearly labeled archives |
| `.github/workflows/` | Unified CI, CodeQL, and tag-only Phone/Watch release pipeline |

Production configuration, credentials, databases, conversations, memory, logs,
and signing material live outside Git through private files and persistent
runtime mounts.

## Quick start

```bash
git clone --branch jarvis/unified-production \
  https://github.com/aaronhall105/jarvis-ai.git
cd jarvis-ai
cp .env.example .env
```

Configure only the services required by the deployment, keep `.env` private,
then follow [INSTALL.md](INSTALL.md). Do not expose an unauthenticated Core to
the public internet.

## Phone and Watch releases

The production-signed Phone and Watch APKs, checksums, public signing reports,
inspection reports, product manifest, and OTA manifest are attached to the
[v19.0.0-alpha25 prerelease](https://github.com/aaronhall105/jarvis-ai/releases/tag/v19.0.0-alpha25).
Install updates in place; do not uninstall or clear application data as an
upgrade workaround.

## Documentation

- [Installation](INSTALL.md)
- [Architecture](ARCHITECTURE.md)
- [Changelog](CHANGELOG.md)
- [Validation status](TESTED.md)
- [Documentation index](docs/README.md)
- [Product baseline](docs/JARVIS_PRODUCT_BASELINE.md)
- [Integrations & Accounts](docs/INTEGRATIONS_ACCOUNTS_V1.md)
- [External Agent Platform](docs/EXTERNAL_AGENT_PLATFORM.md)
- [Developer mode](docs/developer-mode.md)
- [Android OTA releases](docs/ANDROID_OTA_RELEASES.md)
- [Wear endpoint](docs/wear-endpoint-v1.md)
- [Alpha25 release notes](docs/releases/CHANGES_V19_0_0_ALPHA25.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

## Security

Never commit `.env`, access or refresh tokens, OAuth codes, Home Assistant
credentials, private keys, signing files, runtime databases, personal logs,
conversation data, or household imagery. Report vulnerabilities through the
private process in [SECURITY.md](SECURITY.md).
