# Jarvis 19.0.0-alpha33

This release makes Task Centre truthful when finite backlog work transitions
into an ongoing monitoring service.

## Task lifecycle

- Task Centre now exposes `MONITORING` as an active, non-terminal status and a
  generic `CONTINUOUS` work mode.
- Bounded work can remain determinate while it is running. Continuous work has
  no active completion percentage, remaining count, or ETA.
- Completed bounded-phase outcomes are frozen in Task Centre projection state,
  so later incremental activity does not rewrite the historical result.
- Important-Only Inbox shows its initial cleanup outcome separately from its
  ongoing Gmail and Outlook monitoring state. Existing policy, checkpoints,
  receipts, classifications, and recoverable-cleanup authority are unchanged.

## Android experience

- The Tasks screen renders historical bounded-phase summaries without showing
  stale estimate-based progress for continuous monitoring.
- Successful authenticated Task API refresh time is displayed as `Synced`,
  independently from the time of the last underlying task activity.
- Offline snapshots remain visible and are explicitly labelled with their last
  successful sync time while Jarvis reconnects.
- The primary Active, Waiting, Scheduled, and Completed filters share one
  responsive row at Galaxy S21+ and narrower supported widths.
- Task details distinguish initial work, task activity, API sync freshness,
  provider state, and policy state.

## Compatibility and safety

- Phone and Wear share versionCode `190350` and realtime protocol `2`.
- The update is signed with the existing production identity and installs in
  place without clearing app data, authentication, conversations, or endpoint
  preferences.
- No mailbox execution semantics or permanent-delete capability are changed.
