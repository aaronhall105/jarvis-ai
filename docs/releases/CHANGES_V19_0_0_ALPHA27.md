# Jarvis 19.0.0-alpha27 — Google Personal Integrations v1

Alpha27 packages the verified Google Personal Integrations v1 implementation as
one unified Core, Developer gateway, Android Phone, Wear OS, product manifest,
and OTA release. Phone and Watch retain package `com.aaron.jarvisvoice`, the
existing data-compatible application lineage, and realtime protocol `2`.

Core application version remains `3.7.0` because Google capabilities use the
existing authenticated Core APIs, capability registry, Personal Assistant
jobs, receipts, conversation store, and realtime compatibility contract.

## Google Personal Integrations v1

- Authorization Code OAuth with PKCE, one-time expiring state, exact redirect
  validation, replay protection, encrypted principal-scoped credentials,
  refresh handling, revocation, reconnect, and truthful provider health.
- Gmail inbox/read/search/thread grounding with unread, sender, subject, and
  date filtering; draft, reply-draft, send, forward, and archive operations use
  explicit authorization and provider verification.
- Google Calendar list/range/search/timezone/free-busy plus verified
  create/update/cancel operations with retry fencing and semantic RFC3339
  readback comparison.
- Google Contacts search and ambiguity-safe resolution without invented contact
  data; Contacts remain read-only in this version.
- Durable Gmail monitoring through the existing Personal Assistant lifecycle,
  pinned to the authenticated provider account and originating principal and
  conversation.
- Existing capability states remain truthful: configured credentials alone do
  not imply Connected, and Microsoft remains Setup Required.

## Android Integrations fix

Connected Google, Gmail, Calendar, and Contacts cards no longer render the
literal text `null` when optional provider detail is absent. Null, missing,
blank, and literal-null values render cleanly. Google may show the verified
account email as non-secret identity metadata; other services show a bounded
provider-health summary. OAuth tokens, provider subject identifiers, secrets,
and credential material are never displayed.

## Live validation

Google Personal Integrations v1 completed controlled production validation
before release preparation:

- Google identity and independent Gmail, Calendar, and Contacts probes remained
  connected and healthy across a controlled Core restart.
- Gmail inbox/read/thread, unread, sender, and subject searches returned real
  provider evidence without exposing message content unnecessarily.
- A temporary self-addressed draft and a reply draft were created and verified
  as drafts, not sent messages.
- One explicitly approved self-addressed monitor test email was sent exactly
  once. Gmail provider readback verified the sent state and one send receipt.
- Calendar timezone, date ranges, free/busy, temporary create, update, and
  cancel passed provider readback. The temporary event was removed.
- One durable Gmail monitor survived restart, detected its matching message,
  completed exactly once, produced one verified completion receipt, and added
  one assistant completion message to the originating conversation. Later
  worker cycles produced no duplicate trigger or message.
- All Core and speaker SQLite stores passed `PRAGMA quick_check`; conversations,
  memory, jobs, receipts, account state, OAuth sessions, and encrypted
  credentials remained preserved.

The final regression run recorded 748 passing tests, 3 intentional skips, and
133 passing subtests. Ruff, formatting, mypy, compileall, Bandit, dependency
audit, actionlint, gitleaks, product-baseline, Home Assistant, Phone, Wear,
Android lint/build, GitHub CI, and CodeQL checks passed with zero open CodeQL
alerts.

Live Contacts API health and no-result behavior passed, but the connected
account did not contain suitable test data for both unique and ambiguous-name
resolution. Automated coverage verifies both fail-closed paths; no contacts
were invented or modified.

Publishing alpha27 does not claim physical Phone or Watch installation. Both
clients require a later in-place OTA validation and must not be uninstalled or
have application data cleared.
