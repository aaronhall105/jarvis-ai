# Realtime Voice Engine v17.2.0-r1

v17.2.0-r1 replaces the prototype Android sentence-recognition loop with a persistent, full-duplex audio session.

## Data path

```text
Samsung microphone
  → Android AudioRecord (24 kHz PCM16, voice communication mode)
  → authenticated Jarvis Core WebSocket
  → OpenAI Realtime session
  → streamed PCM16 response
  → Android AudioTrack
```

Private home operations use a single `jarvis_command` function. The Realtime model requests that function only when a command needs Home Assistant, Jarvis schedules, routines, reminders or persistent memory. Jarvis Core executes the request through its existing authenticated and verified command path, then sends the result back into the live conversation.

## Interruption

Semantic VAD is configured with response interruption enabled. When Aaron starts speaking during playback:

1. audio continues streaming from the microphone;
2. the Realtime service emits speech-started;
3. the APK increments its playback generation and immediately pauses and flushes queued audio;
4. stale audio chunks from the cancelled response are discarded;
5. the newest request becomes the active turn.

## Security boundary

The APK stores only a dedicated mobile voice token encrypted by Android Keystore. The OpenAI API key remains in Jarvis Core's `.env`. The status endpoint never returns either secret.

## Scope

This release targets ChatGPT-style quality after the user starts a live session. A low-power, always-on local wake-word detector is intentionally isolated for the following release so it cannot destabilise the realtime audio path.
