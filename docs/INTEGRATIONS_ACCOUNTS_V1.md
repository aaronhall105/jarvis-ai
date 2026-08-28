# Integrations & Accounts v1

Jarvis Core owns OAuth sessions and encrypted provider credentials. The Android app opens the
system browser and receives only a success/failure deep link; Google passwords and OAuth tokens
never enter the app, model context, action receipts, or public status APIs.

## Google Cloud setup

1. Create or select a Google Cloud project and enable the Gmail API, Google Calendar API, and
   People API.
2. Configure the OAuth consent screen. Use an Internal audience only for a Google Workspace
   organisation that owns every intended user; otherwise use External and add the intended Google
   accounts as test users until the app is published. Supply the required app identity, support
   email, privacy-policy details, and authorised domains.
3. Create an OAuth 2.0 Web application client. Add exactly the Core callback URL as an authorised
   redirect URI. Production must use HTTPS. Loopback HTTP is accepted only for `localhost` or
   `127.0.0.1` deterministic development. Configure every reverse proxy in front of Core to
   suppress or redact the callback query string from access logs; Core applies the same redaction
   to its own Uvicorn access logger.
4. Configure these Core host values (never commit their real values):

   - `JARVIS_GOOGLE_OAUTH_CLIENT_ID`: the non-secret OAuth client ID.
   - `JARVIS_GOOGLE_OAUTH_CLIENT_SECRET`: the OAuth client secret, supplied through the protected
     deployment environment or a secret manager.
   - `JARVIS_GOOGLE_OAUTH_REDIRECT_URI`: the exact authorised HTTPS callback, normally
     `https://<core-host>/api/integrations/google/callback`.
   - `JARVIS_GOOGLE_ANDROID_RETURN_URI`: keep `jarvis://integrations/google` unless the Android
     manifest and Core configuration are deliberately changed together.
   - `JARVIS_INTEGRATIONS_OWNER_PRINCIPAL`: the server-owned Jarvis user key, for example `aaron`.
   - `JARVIS_CREDENTIAL_ENCRYPTION_KEY`: a URL-safe base64 encoding of exactly 32 random bytes.
     Generate it outside Git with
     `python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"`.
   - `JARVIS_MOBILE_VOICE_TOKEN`: the existing Android/Core bearer token. Android stores it with
     the platform keystore; Core maps it to the owner principal and never accepts a principal from
     the mobile request body.

The minimum selectable permissions are OpenID identity/email plus:

- Gmail read: `gmail.readonly`
- Gmail drafts/sends: `gmail.compose`
- Gmail archive: `gmail.modify`
- Calendar read/free-busy/timezone: `calendar.readonly`
- Calendar writes: `calendar.events`
- Contacts: `contacts.readonly`

Partial grants remain partial. A stored token is not displayed as Connected until a live Google
identity health check succeeds. Revoked or expired refresh credentials are shown as Reconnect
required. Missing host configuration is shown as Setup required.

## Persistence and deployment

Account metadata and AES-256-GCM credential envelopes are stored in the existing Core data volume
at `/app/data/jarvis_integration_accounts.db`. OAuth PKCE verifiers are encrypted, state values are
stored only as SHA-256 hashes, and callbacks are one-time. Preserve the existing `./data:/app/data`
volume during rebuilds; do not copy its database or encryption key into Git or Android assets.

Gmail and Calendar writes run only through the connector registry. The registry creates a durable,
principal-bound idempotency receipt before calling Google and records success only after a provider
read-back verifies the message, draft, archive state, or calendar event. Durable email monitoring
uses the existing follow-up database and delivers once to the original scoped conversation.
