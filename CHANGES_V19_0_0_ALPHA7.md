# Jarvis Android v19.0.0-alpha7

Alpha7 replaces the single flat local message list with real saved
conversations while preserving the voice, Core, failover and performance
foundation delivered through alpha6.

## Conversation archive

- Migrates the existing `messages_v1800` history into the first saved chat.
- Stores separate conversations keyed by the authoritative Core conversation
  ID.
- Re-keys the local archive when Jarvis Core returns an updated conversation
  ID.
- Keeps each chat title, owner, created date, last-used date, pin state,
  preview and message history.
- Isolates Aaron and Amber histories by profile.
- Preserves up to 60 conversations and 300 messages per conversation.

## Chat history interface

- Adds a history button to the main Jarvis header.
- Adds searchable Today, Previous 7 days and Older sections.
- Supports open, rename, pin, unpin and delete.
- Starts a new conversation without deleting older chats.
- Restores the correct Core conversation when an old chat is reopened.

## Composer and messages

- Adds a multiline expanding composer.
- Adds stop-generation while Jarvis is responding.
- Adds long-press copy, edit and resend, retry answer and delete actions.
- Adds a visible Copy action below assistant messages.
- Adds lightweight headings, lists, bold, inline code, code blocks and links.

## Release safety

- Includes the alpha6 regression-test correction so connectivity tests do not
  pin later releases to the old alpha5.1 label.
- Retains Standard and Live modes, automatic follow-up, barge-in, echo
  protection, offline wake word, LAN/Tailscale failover, trusted London time,
  alpha6 diagnostics and the built-in system test.
