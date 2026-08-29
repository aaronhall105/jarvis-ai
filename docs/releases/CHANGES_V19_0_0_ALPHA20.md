# Jarvis 19.0.0-alpha20 — Integrations UI update

Alpha20 is the in-place Android update for Jarvis Integrations & Accounts v1.
It uses a new monotonic Android version identity so phones already reporting
Alpha19 can acquire the verified integrations UI without uninstalling Jarvis or
clearing app data.

## Android settings and integrations

- Adds a top-level expandable **Integrations** section to the existing Jarvis
  Settings screen.
- Opens the existing Integrations & Accounts activity for Google, Gmail,
  Calendar, Contacts, durable email monitoring, and external services.
- Keeps provider states evidence-based: Google remains **Setup required** and
  Gmail, Calendar, and Contacts remain **Not connected** until Jarvis Core has
  genuine OAuth configuration, account state, and provider health evidence.
- Retains the existing `com.aaron.jarvisvoice` application identity, voice,
  default-assistant, overlay, chats, settings, and in-place update path.

## Verification and security

- Adds runtime Android UI coverage for the Settings entry, activity routing,
  manifest/deep-link resolution, Setup Required rendering, and prevention of a
  false Connected state.
- Retains the Integrations & Accounts v1 account platform, Google OAuth, Gmail,
  Calendar, Contacts, durable monitoring, permission enforcement, action
  receipts, and security hardening from the verified feature source.
- Google OAuth credentials are not bundled. Google remains **Setup required**
  until its client configuration is installed securely in Jarvis Core.
