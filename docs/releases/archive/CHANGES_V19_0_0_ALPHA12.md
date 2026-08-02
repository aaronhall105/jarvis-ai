# Jarvis v19.0.0-alpha12 — Voice Ownership and Room Context

## Wake word

- Restores the dedicated offline single-word “Jarvis” detector.
- Restores the stronger sensitivity used before Alpha11.
- Keeps silent command verification after the wake detection.
- Starts command capture sooner after the offline detector fires.
- False detections still return silently to wake-word standby.

## Original voice

- Restores the Home Assistant original Jarvis voice as the default.
- Keeps alternative realtime voices available in Settings.

## Conversation control

- Adds a visible trash button to the main chat.
- Requires confirmation before deleting the current chat.
- Starts a fresh Core conversation after deletion.

## Person room context

- “In what room?” now resolves Aaron or Amber from the preceding turn.
- Uses fresh Home Assistant person states.
- Uses live indoor camera snapshots when vision is configured.
- Falls back to recent person-detection events.
- Never identifies a person by face.
- Only attributes the room to a named person when that person is the sole
  household member marked home; otherwise it preserves uncertainty.

## Background speech and interruption

- Automatic follow-up listening uses a short wake-owned window.
- Low-confidence background speech is ignored.
- Unknown-confidence speech needs a meaningful three-word command.
- Saying “Jarvis” explicitly overrides the confidence guard.
- Phone-speaker barge-in arms sooner.
- Two matching partials of at least two words can interrupt playback.
- Echo suppression remains route-aware.

This is session ownership and confidence filtering, not biometric voiceprint
identification.
