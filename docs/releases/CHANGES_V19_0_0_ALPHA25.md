# Jarvis 19.0.0-alpha25 — unified product revision

This release builds Jarvis Core, Developer gateway, Phone and Watch from one
authoritative `jarvis/unified-production` product revision. It preserves the
existing Phone and Watch application identities, interfaces, data schemas and
in-place update compatibility.

## Included

- The unified Jarvis Brain, conversation, memory, realtime and capability
  architecture used by Phone and Watch.
- The current Phone interface, chat history, Developer tools, Integrations,
  assistant, overlay, wake-word, voice and update functionality.
- The current Watch interface, Tile, assistant, voice, channel manager and
  shared Phone/Watch protocol.
- Google, Gmail, Calendar, Contacts, Home Assistant, External Agent, durable
  work and verified action-receipt capabilities.
- Installed OTA integrity reporting based on the checksum, package, version and
  production signing identity verified during the in-place update.

Google, Gmail, Calendar, Contacts and Microsoft remain **Setup Required** until
their credentials are configured and provider health is verified.
