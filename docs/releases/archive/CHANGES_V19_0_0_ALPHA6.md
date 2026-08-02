# Jarvis Android v19.0.0-alpha6

Alpha6 hardens and measures the voice foundation already delivered in the
v18.4 and v19 alpha releases. It does not replace continuous conversation,
barge-in, self-echo protection, Core-authoritative context, or LAN/Tailscale
failover.

## Added

- Rolling end-to-end turn performance diagnostics for the latest 20 turns.
- Brain-start, first-token, first-audio, total-turn, median and worst latency.
- Dropped realtime audio-frame accounting when WebSocket backpressure is high.
- Expected recovery-transition classification so normal barge-in and network
  recovery do not inflate genuine ordering-warning counts.
- Voice-service lifecycle markers for restart and task-removal diagnosis.
- Live Android input/output route monitoring for Bluetooth, wired, USB and
  built-in audio changes.
- A one-button Jarvis system test in Settings that checks LAN health,
  Tailscale health, endpoint selection, microphone permission, audio output,
  Europe/London local time and conversation continuity evidence.
- New regression tests for performance telemetry and transition
  classification.

## Preserved

- Standard and Live conversation modes.
- Automatic follow-up listening.
- Interruption while thinking or speaking.
- Delayed self-echo suppression.
- Default Android assistant and compact overlay.
- Offline Sherpa wake word.
- Core-authoritative conversation ID, memory and Home Assistant tools.
- LAN-first and Tailscale fallback with automatic return home.
