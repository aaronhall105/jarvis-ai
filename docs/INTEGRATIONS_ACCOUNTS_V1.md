# Integrations & Accounts

Jarvis Core owns OAuth sessions and encrypted provider credentials. The Android app opens the
system browser and receives only a success/failure deep link; Google passwords and OAuth tokens
never enter the app, model context, action receipts, or public status APIs. Google personal
integrations reuse the unified capability registry, planner, action receipts, Personal Assistant
durable jobs, and same-conversation delivery; they do not have a separate scheduler or identity
model.

## Current state

The Google connector implementation supports OAuth, Gmail, Calendar, Contacts, and durable email
monitoring. Automated provider-contract, persistence, isolation, failure, and security tests use
deterministic provider responses. This does **not** mean a deployment is connected: until the host
has an OAuth client and a user completes consent, Android and Core must report **Setup required**.
Microsoft remains Setup required and is not part of Google Personal Integrations v1.

Connection state is evidence based:

- **Setup required / Not connected**: host OAuth configuration or a principal account is absent.
- **Connecting**: a one-time OAuth transaction is pending.
- **Connected / Partial permissions**: identity and each exposed Google product passed a live API
  probe with the required scopes.
- **Provider unavailable / Degraded**: credentials remain authenticated but a product probe fails.
- **Reconnect required**: token refresh is rejected or the grant is revoked.

An encrypted token row alone is never proof that Gmail, Calendar, or Contacts is healthy.

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
identity check and the independently granted Gmail, Calendar, and Contacts probes succeed. Revoked
or expired refresh credentials are shown as Reconnect required. Missing host configuration is
shown as Setup required.

The relevant primary Google references are:

- [OAuth 2.0 for web server applications](https://developers.google.com/identity/protocols/oauth2/web-server)
- [Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)
- [Calendar scopes](https://developers.google.com/workspace/calendar/api/auth)
- [People connections API](https://developers.google.com/people/api/rest/v1/people.connections/list)

The Gmail scopes above are classified by Google as restricted scopes. A private test-mode OAuth
app can use explicitly listed test users; wider distribution can require Google's consent-screen
verification. Request only the features needed by the principal and reconnect to add permissions.

## Android connection flow

The existing Integrations screen calls the authenticated mobile Core API to create a one-time OAuth
transaction. Android opens the returned Google URL in the system browser. Google returns an
authorization code to the exact HTTPS Core callback; Core claims and consumes the state, exchanges
the code with PKCE, encrypts credentials, verifies the Google identity, and redirects to
`jarvis://integrations/google`. Android receives only the result and refreshes live provider state.
It never receives the authorization code, access token, refresh token, or client secret.

Disconnect is principal-scoped and attempts Google token revocation before removing the locally
owned account. A failed refresh or revoked grant moves the account to Reconnect required instead of
claiming the provider is offline or healthy.

## Gmail

Read capabilities include inbox/recent search, Gmail query search, message metadata and safe body
text, thread retrieval, unread/date/sender/subject filters through Gmail query syntax, and bounded
attachment metadata. Provider message and thread IDs are returned as evidence for conversational
follow-up; Jarvis must never invent a message.

Writes support creating/editing drafts, reply drafts, sending an existing draft, forwarding, and
archiving. Draft and reply operations report **drafted**, never sent. Sending preflights the actual
draft and validates its single recipient and subject before execution. Sends/forwards require an
explicit current user write intent and provider readback must show the exact recipient/subject and
the `SENT` label before Jarvis reports verified success. Archive verification confirms the `INBOX`
label was removed. Unknown write outcomes are fenced and are not blindly retried.

Recipients must be a single syntactically valid address explicitly supplied by the user or flow
from an unambiguous Contacts result in a verified plan. Multiple contact matches require
clarification; no address is inferred.

## Google Calendar

Calendar reads support calendar lists, event lists/ranges, event retrieval, search, free/busy, and
the account timezone. Natural-language dates are resolved by the existing Brain/Personal Assistant
time handling before structured Google arguments are executed.

Writes support create, update/reschedule, and cancel/delete. Create requires a title plus structured
start and end values; Jarvis must ask when a required duration or other consequential field is
ambiguous. Attendee addresses use the same verified-recipient rule as Gmail. Creation uses a stable
provider event ID derived from the principal/conversation-scoped action idempotency key. A retry can
therefore read back a matching existing event instead of creating a duplicate. All writes require
provider readback before a verified action receipt is produced.

## Google Contacts

Contacts search and resolution return provider resource names, names, verified email/phone values,
and organisation information when Google supplies it. Exact single matches can feed Gmail and
Calendar plan steps. No match returns unresolved, and multiple plausible contacts return ambiguous
with the candidates needed for clarification. Contacts are read-only in this version.

## Durable Google monitoring and Personal Assistant work

`gmail.search` and `gmail.thread` are repeatable read capabilities owned by the existing durable
external-monitor path. A monitor stores its principal, scoped conversation, capability, structured
Gmail query, baseline provider IDs, poll/delivery fences, backoff, expiry, and next evaluation. Core
restart preserves the same job. Unchanged observations are filtered deterministically without a
model call, and one new matching provider message is delivered once to the originating conversation
with optional supplemental notification.

Provider state is validated both when the monitor is created and on each execution. Missing scopes,
revoked credentials, or provider failure yield a truthful unavailable/retry/reconnect state; they
never fabricate a match. Calendar reads and approved Google writes can likewise be composed with
the unified planner and durable Personal Assistant lifecycle. There is no Google-specific task
engine.

Google email, calendar, and contact content remains provider data. It is not copied into long-term
Personal Memory unless the user explicitly asks Jarvis to remember a fact under the existing memory
policy.

## Approval, receipts, and diagnostics

Read operations normally need no repeated confirmation after connection. Consequential Gmail and
Calendar writes pass through current-intent validation, capability/scope checks, principal and
account scoping, confirmation policy, one execution boundary, provider verification, and a durable
action receipt. Model-generated text is never execution evidence.

The authenticated mobile provider API returns account identity, granted scopes, per-product state,
safe token presence/expiry metadata, last provider health, and capabilities. It never returns raw
credentials. Active external monitors are available through the existing principal/conversation-
scoped monitor APIs; provider diagnostics must not be added to the general `/health` response with
secret-bearing errors.

## Persistence and deployment

Account metadata and AES-256-GCM credential envelopes are stored in the existing Core data volume
at `/app/data/jarvis_integration_accounts.db`. OAuth PKCE verifiers are encrypted, state values are
stored only as SHA-256 hashes, and callbacks are one-time. Preserve the existing `./data:/app/data`
volume during rebuilds; do not copy its database or encryption key into Git or Android assets.

Gmail and Calendar writes run only through the connector registry. The registry creates a durable,
principal-bound idempotency receipt before calling Google and records success only after a provider
read-back verifies the message, draft, archive state, or calendar event. Durable email monitoring
uses the existing follow-up database and delivers once to the original scoped conversation.

Before a production deployment, preserve the data volume and run database integrity checks. Never
include the integration database, host environment, encryption key, OAuth client secret, bearer
token, or generated callback data in source control, Android resources, diagnostics, or release
artifacts.
