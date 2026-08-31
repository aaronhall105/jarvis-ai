# Jarvis Android Phone and Wear OS clients

This Gradle project builds the current `v19.0.0-alpha25` Phone and Watch
clients for the unified Jarvis Brain/Core. Both clients use package
`com.aaron.jarvisvoice`, versionCode `190270`, realtime protocol `2`, and one
approved product source revision.

## Modules

| Module | Responsibility |
| --- | --- |
| `app/` | Android Phone chat, voice, assistant, overlay, wake word, Settings, Integrations, Developer, diagnostics, and OTA |
| `wear/` | Wear OS activity, Tile, assistant, voice service, audio endpoint, and channel management |
| `wearprotocol/` | Shared Phone/Watch wire messages, endpoint identity, and compatibility tests |

Phone and Watch are endpoints of the same Core. They do not run independent
Jarvis brains or create separate server-side conversation truth.

## Phone client

The Phone client provides:

- the current Jarvis chat interface, persistent local history mirror, streamed
  responses, conversation selection, and safe deletion of only the current chat
- typed, Standard voice, and Live voice interaction
- interruption, barge-in, audio ownership, and bounded recovery
- authenticated LAN/remote endpoint selection and failover diagnostics
- client turn IDs, conversation synchronization, delivery recovery, and
  realtime application ping/pong
- Android default-assistant role, compact overlay, wake-word/background support,
  and foreground-service disclosures
- top-level Integrations settings, `IntegrationsActivity`, provider health, and
  the `jarvis://integrations/google` OAuth return route
- Developer/Codex access through the separately authenticated gateway
- signed in-place OTA updates with checksum, package, version, and signer
  verification

Provider state remains truthful: missing Google or Microsoft configuration is
shown as Setup Required, not Connected or Core offline.

## Wear OS client

The Watch client provides:

- the Jarvis Watch activity and Tile
- assistant and hardware-entry support where configured
- microphone capture, audio playback, continuous turns, and stop/interruption
- authenticated Phone/Core channel handling, generation checks, startup
  buffering, and bounded timeouts
- endpoint and conversation identifiers compatible with the Phone and Core

See [Wear endpoint documentation](../../docs/wear-endpoint-v1.md) for setup and
physical validation guidance.

## Shared Brain and conversation model

Core owns principal identity, conversation history, memory, planning,
capabilities, durable jobs, action verification, and receipts. Device-local
storage is a presentation and recovery layer. A conversation started on one
endpoint can continue on another when both use the same principal and
conversation identifiers.

## Build and test

Use Java 17, Android SDK 36, and the checked-in Gradle wrapper:

```bash
./gradlew --no-daemon \
  :wearprotocol:test \
  :app:testDebugUnitTest :app:lintRelease \
  :wear:testDebugUnitTest :wear:lintRelease \
  :app:assembleDebug :wear:assembleDebug
```

Production release signing occurs only in the approved tag-triggered GitHub
workflow. Do not create a new key, use debug signing for an in-place production
update, or commit keystores/passwords.

## Privacy and data preservation

Model-provider credentials remain on Core. Mobile credentials are protected by
Android Keystore-backed storage. OAuth access/refresh tokens remain encrypted
on Core and are not exposed to Android UI, logs, or model-visible results.

Install updates over the existing applications. Do not uninstall or clear
Phone/Watch data as a workaround for a signing, version, or configuration
problem.
