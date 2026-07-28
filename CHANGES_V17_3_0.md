# Jarvis v17.3.0 — Unified Brain, Voice Selector and Wake Phrase

## Corrected architecture

- Every completed phone voice request is now sent through the existing Jarvis Core central request path before any answer is spoken.
- OpenAI Realtime is restricted to low-latency audio input, transcription, semantic turn detection, interruption and optional speech rendering.
- Automatic model answers are disabled. Realtime no longer decides whether a request is private enough to send to Jarvis Core.
- Jarvis Core conversation IDs are retained across turns so follow-ups continue through the existing memory and dialogue system.
- Transcription guidance includes Aaron, Amber and common smart-home terms to improve private-name recognition.

## Voice choices

- Added **Jarvis — Home Assistant original voice**, using the configured Home Assistant Assist TTS pipeline.
- Added selectable Realtime voices: Marin, Cedar, Alloy, Ash, Ballad, Coral, Echo, Sage, Shimmer and Verse.
- Voice choice is sent when the live session starts; changing voice restarts the session.

## Wake phrase

- Added foreground wake-phrase mode with a configurable phrase, defaulting to `Jarvis`.
- Uses Android on-device speech recognition when available and falls back to the system recogniser when necessary.
- Accepts `Jarvis`, `Hey Jarvis`, `Okay Jarvis`, `OK Jarvis` and limited common recogniser variants.
- Commands spoken in the same phrase can be forwarded immediately.
- After waking, a 45-second follow-up window remains open without repeating the wake phrase.

## Android and release workflow

- Added Home Assistant URL, token and optional pipeline settings for the original Jarvis voice.
- Tokens remain encrypted with Android Keystore.
- Removed the two older Android workflows during installation to prevent duplicate builds.
- Added a v17.3.0 workflow with cached debug signing material for more consistent later test builds.

## Preserved

- Existing Jarvis Core memory, people and presence handling, house awareness, verified controls, routines, schedules, conditional actions and task engine.
- Home Assistant Assist v1.6.2. No Home Assistant integration files are changed.
