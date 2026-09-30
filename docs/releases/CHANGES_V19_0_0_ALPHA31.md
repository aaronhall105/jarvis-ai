# Jarvis 19.0.0-alpha31

This release adds the Unified Task Centre while preserving Jarvis's existing
durable execution, authority, and verification systems.

## What is new

- A first-class **Tasks** tab beside Chat shows active work, tasks waiting for
  Aaron, scheduled work, completed history, and problems.
- Task details show durable plan steps, verified results, real measured
  progress, provider state, waiting reasons, timelines, and only controls that
  are valid for the underlying task.
- Personal Assistant jobs, reminders, recurring work, external monitors,
  Astra/agent plans, and frozen email cleanup operations share one generic,
  principal-scoped view without becoming a second task engine.
- "Notify me when you're done" now attaches to the relevant active task.
  Completion and requested failure notifications are restart-safe and
  deduplicated.
- Unsupported capabilities and provider outages are shown truthfully as
  waiting or blocked work. Jarvis no longer claims background progress merely
  because a reply says it has started.
- Future registered capability domains render through the same Android task
  contract without a domain-specific Task Centre update.

## Safety

- Confirm and Decline continue through the owning task engine's existing
  authority boundary.
- Completed writes and verified provider work are not repeated by generic
  retry controls.
- Notification transport acceptance is not misreported as confirmed handset
  delivery.
- Mobile APIs use the existing principal-scoped bearer model and expose no
  OAuth credentials, administrator token, mailbox IDs, or hidden reasoning.

This is an in-place update from alpha30. It does not clear app data, connected
accounts, conversations, Watch pairing, or voice settings.
