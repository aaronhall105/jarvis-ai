# Jarvis Android v19.0.0-alpha5

Alpha5 connects the existing Jarvis Core tools, scoped memory and persistent
conversation engine to the Android voice client through a structured protocol.

- Synchronises the authoritative conversation ID from Jarvis Core.
- Restores the correct Aaron or Amber conversation after reconnecting.
- Sends privacy-safe Home Assistant tool completion events to Android.
- Never sends raw tool arguments, entity credentials or authentication data.
- Reports whether long-term memory was used during the turn.
- Reports message count and authenticated user context.
- Adds Core context, last tool and memory state to voice diagnostics.
- Keeps Jarvis Core as the only authority for Home Assistant actions.
- Preserves offline wake word, voice state machine, acoustic processing,
  network recovery, interruption, overlay closure and self-echo protection.
- Retains WebSocket PCM as the validated transport while WebRTC remains
  isolated from the trusted Home Assistant and memory control plane.
