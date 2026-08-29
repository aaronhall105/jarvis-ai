# Jarvis 19.0.0-alpha22 — actual alpha17 app with unified product

This release restores the actual Android phone-app experience from alpha17
commit `3279a57ac8593cc2eba70d1678350dea25235a96` while retaining the current
unified product and data schemas.

## Included

- Restores the exact alpha17 Jarvis logo, launcher resources, normal Jarvis
  title, chat layout, composer, House activity, New chat and More options.
- Restores the alpha17 Chat history, Improvements, Delete current chat,
  Settings and message-action navigation.
- Retains the current conversation-aware history schema and safe scoped
  deletion so alpha21 data remains compatible.
- Retains Developer/Codex mode, current realtime recovery, client turn IDs,
  endpoint failover, voice interruption, default assistant, overlay, wake word,
  Wear bridge and signed OTA updater.
- Adds Integrations and Developer sections within the alpha17 Settings
  presentation and retains the existing IntegrationsActivity and
  `jarvis://integrations/google` callback route.
- Keeps the unified Core, External Agent Platform, Home Assistant grounding,
  Memory, durable follow-ups, action receipts, Google OAuth, Gmail, Calendar,
  Contacts and security hardening unchanged.

Google remains **Setup Required** until OAuth credentials are configured
securely in Jarvis Core. No Google password is entered into Jarvis.
