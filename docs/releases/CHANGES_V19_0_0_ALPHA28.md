# Jarvis 19.0.0-alpha28 — Email Assistant v3 and Outlook

Alpha28 publishes the Email Assistant v3 work already approved on
`jarvis/unified-production`. Phone and Watch retain package
`com.aaron.jarvisvoice`, the established production signing identity, and
realtime protocol `2`. Core application version remains `3.7.0`.

## Email Assistant v3

- One multi-provider Email Assistant now keeps Gmail and Outlook/Microsoft 365
  accounts, message identities, conversation focus, receipts, and settings
  isolated by authenticated provider account.
- Gmail and Microsoft Graph support safe read, search, draft, send, reply,
  important-message alerts, reply monitoring, cleanup previews, recoverable
  folder moves, history, and provider-verified receipts where the connected
  account grants the required capability.
- Destructive bulk requests resolve the account first, freeze the exact
  candidate message IDs before confirmation, and resume in bounded,
  idempotent batches. Automatic cleanup remains conservative and never
  permanently deletes mail.
- Provider terminology is explicit and natural: Gmail uses **Bin** and Outlook
  uses **Deleted Items**.

## Safer conversations and monitoring

- Durable structured continuations consume Yes, No, “Do that”, and “Do the
  rest” before generic routing. Confirmations remain principal-, conversation-,
  provider-, account-, and candidate-set-scoped.
- Capability answers are assembled from registered provider capabilities and
  no longer invent mailbox or Outlook-app sync controls.
- Important-email and reply monitoring retain provider-specific incremental
  cursors, deduplication, restart recovery, bounded retry, and outage isolation.
- Gmail history entries whose individual messages have genuinely disappeared
  with HTTP 404 are skipped without poisoning the remaining delta batch;
  authentication, quota, network, and server errors still fail normally.

## Android and Wear

- Integrations / Email Assistant includes Outlook Connect, Reconnect, and
  Disconnect controls plus the Microsoft OAuth browser-return route. The OTA
  build verifies these controls in the compiled Phone APK.
- Phone and Wear include the Android reliability fixes merged since alpha27 and
  are built together from the same immutable production revision.

Outlook support is included, but Outlook is **not yet connected** to Aaron's
Microsoft account. Microsoft application setup and Aaron's explicit browser
OAuth consent are still required before Jarvis can report that account as
Connected. No real Gmail or Outlook message is sent, moved, archived, restored,
or marked read merely to certify this release.
