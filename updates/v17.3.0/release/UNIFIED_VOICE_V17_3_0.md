# Jarvis Unified Voice v17.3.0

## Data path

```text
Phone microphone
  -> Jarvis Core authenticated WebSocket
  -> OpenAI Realtime transcription and semantic VAD
  -> completed transcript
  -> Jarvis Core central request path
  -> memory / people / house awareness / controls / routines / schedules
  -> final Jarvis Core response text
  -> selected Realtime voice OR Home Assistant original TTS voice
  -> phone speaker
```

The Realtime session has `create_response` disabled. It cannot answer a user turn automatically. Each transcript is passed to the same `core._execute_ai_request` path used by the established Jarvis features.

## Why Amber and private context should work again

The previous v17.2.0-r1 design let the Realtime model decide whether to call the Jarvis tool. Requests such as `Where is Amber?` could be answered or misunderstood before Jarvis Core saw them. v17.3.0 sends every completed request to Core, retains the conversation ID, and adds Amber and other private terms to the transcription prompt.

## Voice modes

### Realtime voices

Realtime voices retain streamed 24 kHz PCM playback and immediate barge-in. Available built-in choices are Marin, Cedar, Alloy, Ash, Ballad, Coral, Echo, Sage, Shimmer and Verse.

### Original Jarvis voice

The app sends Jarvis Core's final response text to Home Assistant using an Assist pipeline run beginning at the TTS stage. Home Assistant returns the configured TTS media URL, which the phone plays using the saved Home Assistant token. This requires the Home Assistant URL, long-lived token and optionally a pipeline ID in the app.

## Wake phrase behaviour

When wake mode is armed, the full Realtime microphone stream is closed and Android speech recognition listens for the configured prefix. On-device recognition is preferred where the phone supports it. A detected wake phrase opens the live Realtime session. Follow-ups remain active for 45 seconds after the response completes.

This wake stage is intentionally separate from live conversation to avoid running two microphone consumers simultaneously. Android documents that `SpeechRecognizer` is not intended as a permanent high-duty continuous recogniser, so this release uses it conservatively for the sleeping wake stage rather than for the full conversation.

## Security

- OpenAI API key remains on Jarvis Core.
- Mobile and Home Assistant tokens are encrypted with Android Keystore.
- The phone cannot choose a different Core user or elevate itself to administrator.
- Home Assistant integration files are not modified.
