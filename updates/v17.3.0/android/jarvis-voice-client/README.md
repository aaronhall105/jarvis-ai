# Jarvis Unified Voice v17.3.0

Android voice client for Aaron's private Jarvis Core.

## Architecture

- OpenAI Realtime provides low-latency microphone transcription, semantic turn detection and optional streamed voices.
- Every completed request is sent through the existing Jarvis Core central request path before any reply is spoken.
- Jarvis Core remains authoritative for memory, people such as Amber, house awareness, verified Home Assistant controls, routines, schedules and follow-up context.
- Realtime never answers user requests independently in this release.

## Voice choices

- **Jarvis — Home Assistant original voice** uses the configured Home Assistant Assist pipeline for TTS.
- Realtime voices: Marin, Cedar, Alloy, Ash, Ballad, Coral, Echo, Sage, Shimmer and Verse.

Changing the voice restarts the live session because a Realtime voice cannot be changed after audio has been produced in that session.

## Wake phrase mode

When enabled, the foreground service arms Android's on-device speech recogniser where available and listens for the configured phrase, normally `Jarvis`. A phrase such as `Jarvis, where is Amber?` can wake the live session and forward the command. The live follow-up window then remains open temporarily without repeating the wake phrase.

Android's `SpeechRecognizer` is used only for the sleeping wake-phrase stage. Full live conversation uses the 24 kHz Realtime audio path.

## First setup

1. Enter the Jarvis Core URL and mobile voice token.
2. Choose a voice.
3. Leave wake-word mode enabled and keep the wake phrase as `Jarvis`, or disable it for immediate live listening.
4. For the original Jarvis voice, also enter the Home Assistant URL, long-lived access token and optional Assist pipeline ID.
5. Tap **Arm / Start Jarvis** and grant microphone and notification permissions.

## Build

With Android SDK 36 and Gradle 9.4.1:

```bash
gradle --no-daemon :app:testDebugUnitTest :app:assembleDebug
```

The APK is written to `app/build/outputs/apk/debug/app-debug.apk`.
