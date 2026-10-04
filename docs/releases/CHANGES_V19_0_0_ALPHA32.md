# Jarvis 19.0.0-alpha32

This release makes Android Core connectivity resilient to a changing LAN
address and turns Task Centre progress into a structured, durable product
contract.

## What is new

- A single process-wide endpoint manager supplies Tasks, Chat, realtime voice,
  integrations, proactive features, and improvement APIs.
- The manager prefers the current authenticated last-known-good route, verifies
  configured alternatives through Core health identity, and accepts only
  explicitly configured LAN or remote/Tailscale endpoints.
- Tasks retain the latest principal-scoped, redacted snapshot during a temporary
  disconnect and show a clear stale/offline state instead of raw socket errors.
- Task cards and details render optional determinate or indeterminate progress,
  remaining work, primary metrics, and provider/subtask breakdowns.
- ETA is derived from a bounded rolling history of actual progress, smoothed
  across observations, and suppressed when evidence is insufficient or work is
  paused, waiting, offline, or complete.
- Empty waiting reasons no longer render as `Why: null`, and the filter row is
  horizontally accessible on narrow phone displays.

## Safety and continuity

- Existing Android credentials, application data, conversations, Watch pairing,
  and settings are preserved by the in-place upgrade.
- Last-known-good endpoints are persisted only after an authenticated protected
  API or realtime connection succeeds; health alone does not establish trust.
- Public cleartext endpoints and embedded URL credentials remain rejected.
- Task progress is a projection only. Important-Only Inbox execution authority,
  provider checkpoints, receipts, classification, and permanent-delete
  protections are unchanged.
- Phone and Wear remain on realtime protocol `2` and share versionCode `190340`.
