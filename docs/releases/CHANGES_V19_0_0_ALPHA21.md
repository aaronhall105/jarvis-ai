# Jarvis 19.0.0-alpha21 — unified production baseline

This release reconciles the divergent Jarvis product lines into one source baseline.

## Included

- Restores the latest Jarvis Android experience, branding, persistent chat history, delete-current-chat action, Developer/Codex mode, voice ownership fencing, endpoint routing, and durable realtime recovery.
- Adds the top-level Integrations settings section and existing Integrations & Accounts activity to that current Android app.
- Restores the Wear OS application, Tile, assistant endpoint, shared phone/watch protocol, and audio routing.
- Preserves the verified External Agent Platform, planner, capability validation, action receipts, Google OAuth, Gmail, Calendar, Contacts, durable email monitoring, Home Assistant grounding, and security controls.
- Adds a persistent Core realtime turn ledger, principal-scoped conversation APIs, conversation-deletion follow-up cancellation, and additional Home Assistant/presence verification hardening.
- Locks future release workflows to `jarvis/unified-production` and adds deterministic product-baseline assertions.

Google remains **Setup Required** until the Google Cloud OAuth client is configured securely in Jarvis Core. No Google password is entered into Jarvis.
