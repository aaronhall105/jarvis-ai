# Jarvis v17.2.0-r1 — Realtime Voice Engine

## Added

- Full-duplex 24 kHz PCM phone audio through a persistent Jarvis Core WebSocket.
- OpenAI Realtime session proxy with semantic voice activity detection.
- Server-side interruption enabled so new speech cancels the current response.
- Streamed PCM playback in the Android app with immediate playback flushing.
- Android `VOICE_COMMUNICATION` capture with acoustic echo cancellation, noise suppression and automatic gain control when supported.
- A dedicated authenticated mobile voice token; the OpenAI API key remains on Jarvis Core.
- Realtime tool bridge into Jarvis Core's existing private command path.
- GitHub Actions unit-test and APK build workflow.

## Preserved

- Existing Android package ID, allowing installation over v17.1.0.
- Jarvis Core scheduling, routines, memory, house awareness and verified Home Assistant tools.
- Home Assistant Assist v1.6.2. This release makes no Home Assistant integration changes.

## Deliberately deferred

- Always-on local wake-word detection while the app is idle. v17.2.0-r1 focuses on a high-quality active live session: tap Start once, then converse naturally and interrupt at any point.
