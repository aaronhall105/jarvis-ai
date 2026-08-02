# Jarvis Android v19.0.0-alpha5.1

Alpha5.1 is the connectivity and trusted-local-time maintenance release built on the validated alpha5 voice and context foundation.

- Keeps the configured home LAN Core URL as the primary endpoint.
- Adds `http://100.127.215.111:8000` as the Tailscale fallback.
- Checks the real Jarvis Core `/health` endpoint instead of treating general internet availability as Core availability.
- Selects Tailscale immediately when Android is no longer using Wi-Fi or Ethernet.
- Rechecks the LAN endpoint while remote and automatically returns to it when reachable.
- Preserves the authenticated user and authoritative conversation ID across endpoint changes.
- Separates network-online and Core-reachable states in diagnostics.
- Shows the active endpoint and safe host/port in diagnostics.
- Adds a diagnostics-counter reset button without clearing current conversation or endpoint state.
- Sends `Europe/London` and an offset-aware local datetime with mobile control messages.
- Recomputes trusted London date and time inside Jarvis Core for every brain turn, including automatic GMT/BST handling.
- Keeps WebSocket PCM, offline Sherpa wake, overlay behaviour, interruption, echo protection, memory and Home Assistant authority unchanged.

The GitHub Actions build applies the alpha5.1 source patch to a clean alpha5 checkout, runs Core and Android regression tests, and publishes `jarvis-assistant-v19.0.0-alpha5.1-debug.apk`.
