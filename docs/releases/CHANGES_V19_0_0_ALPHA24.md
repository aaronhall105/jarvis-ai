# Jarvis 19.0.0-alpha24 — phone/Core communication repair

This release preserves the unified alpha23 product while correcting confirmed
physical-phone communication failures without changing app identity or user
data.

## Fixed

- Distinguishes a reachable Core authentication rejection from a Core outage in
  Integrations & Accounts.
- Preserves a meaningful LAN HTTP 401/403 instead of hiding it behind a later
  remote transport failure.
- Classifies HTTP status before reading an error response body, preventing a
  truncated remote body from replacing the authoritative authentication error.
- Extends only the integrations health-refresh request budget to cover the
  measured 13.8–26.3 second live Core provider refresh.
- Completes server-initiated WebSocket close handshakes and preserves realtime
  `auth.error` instead of allowing a transport-ping timeout to overwrite it.
- Uses the Jarvis application ping/pong as the authoritative realtime heartbeat
  with an explicit bounded pong deadline.
- Migrates the legacy encrypted mobile-token preference in place, using the
  existing Android Keystore identity and never overwriting a current token.
- Adds safe one-way token fingerprints to Core rejection logs for diagnosis;
  credential values are never logged.

Google, Gmail, Calendar and Contacts remain **Setup Required** until Google
OAuth is securely configured and provider health is verified.
