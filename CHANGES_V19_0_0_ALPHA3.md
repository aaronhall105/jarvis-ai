# Jarvis Android v19.0.0-alpha3

Cumulative voice-foundation and realtime-resilience build.

- Includes the alpha2 state machine and native input processing foundation.
- Adds network-aware WebSocket lifecycle management.
- Pauses reconnect loops while Android reports no network.
- Reconnects immediately when connectivity returns.
- Adds authentication and session-ready watchdogs.
- Uses bounded exponential reconnect with jitter.
- Measures connection latency, WebSocket round-trip time and first-audio delay.
- Shows the measurements in the existing Voice foundation diagnostics card.
- Keeps the current WebSocket PCM transport as the production fallback.
- Preserves offline Sherpa wake, Standard recognition, overlay behaviour,
  Aaron/Amber isolation, Home Assistant tools and conversation closure.

Alpha3 is the final resilience layer before introducing an opt-in WebRTC
transport in alpha4. WebSocket PCM remains authoritative in this release.
