# Jarvis Android Assistant

The Android application is the mobile and system-assistant surface for Jarvis
Core.

The current default branch is part of the `19.0.0-alpha17` Jarvis release line.
The authoritative product identity is shared with Core through the repository's
release/version checks.

## Responsibilities

The Android client owns:

- typed chat and persistent on-device presentation of conversation history;
- microphone capture and speech-recognition entry points;
- offline wake phrase detection and background re-arming;
- Android default-assistant integration;
- compact overlay invocation above the current application;
- realtime text/audio transport to Jarvis Core;
- playback, interruption and echo suppression;
- connection failover, diagnostics and user-facing settings.

The Android application does **not** own long-term Jarvis memory, model API keys
or smart-home authority. Those remain on Jarvis Core/Home Assistant.

## Assistant integration

Jarvis implements Android assistant entry points so it can be selected as the
device's digital assistant.

Assistant invocation can open a compact overlay instead of forcing the full chat
activity to the foreground.

## Voice

Voice behaviour is split across Android and Core:

- Android captures speech, handles wake lifecycle and plays assistant audio;
- Core owns conversational state, model orchestration and realtime voice events;
- current Alpha17 work restores the original ElevenLabs Jarvis voice route and
  continues playback/echo hardening.

Voice handling remains alpha software and is actively tested against
interruption, reconnect and wake-recovery edge cases.

## Privacy and credentials

The OpenAI/model API key remains on Jarvis Core.

Mobile-side secrets and optional Home Assistant credentials are stored through
Android's secure storage mechanisms. Do not commit tokens, signing keys or
personal diagnostics to the repository.

See [`PRIVACY.md`](PRIVACY.md) and the repository
[`SECURITY.md`](../../SECURITY.md).

## Build

The root CI workflow builds this project with Java 17, Android SDK 36 and Gradle
9.4.1 and runs Android unit tests before assembling the debug APK.

Published packages are distributed through the repository's release/OTA
workflows rather than being committed as source files.
