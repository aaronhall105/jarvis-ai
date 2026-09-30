# Jarvis Unified Task Centre

The Task Centre is a principal-scoped, provider-neutral view of durable Jarvis
work. It is not a second task engine. Personal Assistant jobs, recurring work,
external monitors, Astra/agent plans, and frozen email operations retain their
own state machines and execution rules.

Every source is projected into one redacted `WorkItem` contract with a stable
source-prefixed identity, normalized status, measurable progress, current and
planned steps, waiting evidence, valid controls, provider/capability metadata,
and an evidence-backed timeline. Android renders that contract generically; a
new registered capability does not require Task Centre UI code.

The existing durable agent planner is the general autonomous work runtime. It
discovers registered capability health, validates dynamic dependency graphs,
enforces confirmation and write authority, executes only grounded steps,
persists receipts, reconciles uncertain writes after restart, preserves
completed evidence during replanning, and resumes safe work idempotently.

Task Centre controls delegate to the owning engine. In particular, Confirm and
Decline approve exactly one persisted authority gate; they never turn UI prose
into execution authority. Retry delegates to the source engine and does not
repeat completed provider work.

Completion and failure subscriptions use Jarvis's existing principal-scoped
mobile notification transport. A durable delivery ledger is written before
submission. A restart in that window becomes `outcome_unknown` and is never
blindly resent. Home Assistant service acceptance is recorded as
`accepted_unverified`, not as handset delivery.

The mobile API uses the existing mobile bearer identity. It never exposes the
integrations administrator token, OAuth material, raw mailbox message IDs, or
model chain-of-thought.
