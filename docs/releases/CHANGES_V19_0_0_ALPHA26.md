# Jarvis 19.0.0-alpha26 — Personal Assistant v1

Alpha26 packages the verified Personal Assistant v1 implementation as one
unified Core, Developer gateway, Android Phone, Wear OS, product-manifest, and
OTA release. Phone and Watch retain package `com.aaron.jarvisvoice`, the
existing data-compatible application lineage, and realtime protocol `2`.

Core application version remains `3.7.0` because this release adds capability
without changing the existing Core/client compatibility contract.

## Personal Assistant v1

- Timezone-aware natural-language reminders and durable scheduled work.
- Structured recurring reminders and tasks that survive Core restart.
- Capability-backed condition monitoring, including verified Home Assistant
  entity-state conditions.
- Conversational cancellation, pause, resume, rescheduling, and task status.
- Completion delivery to the same durable conversation that created the job;
  mobile notification remains supplemental.
- Execution, occurrence, receipt, and conversation-delivery idempotency.
- Principal-scoped explicit personal memory with durable save, bounded recall,
  correction, and forgetting.
- Principal isolation for jobs, conversations, memory, and management APIs.
- Capability validation at creation and execution, with truthful Setup
  Required, unavailable, failed, accepted-but-unverified, and verified states.

## Live validation

Personal Assistant v1 completed live Core validation before release
preparation:

- a reminder persisted before acknowledgement, executed once, and posted once
  to its originating conversation;
- a second reminder survived a Core restart and retained its job, principal,
  conversation, and schedule identity;
- recurring occurrences executed once each across a restart and were then
  cancelled with a verified receipt;
- cancellation fenced execution and rescheduling fenced the old occurrence;
- an unchanged Home Assistant condition produced no message, then a verified
  state transition produced exactly one conversation completion and receipt;
- explicit memory survived restart, accepted a correction without conflicting
  current facts, and no longer returned the retired fact after forgetting;
- duplicate request and delivery fences produced one durable job, one
  execution, and one completion;
- notification failures remained supplemental and truthful, while an available
  transport reported accepted-but-unverified rather than physical delivery.

The final validation recorded 736 passing Python tests, 3 intentional skips,
and 133 passing subtests, plus Ruff, formatting, mypy, compile, Bandit,
dependency audit, product-baseline, Home Assistant, GitHub CI, Android CI, and
CodeQL gates.

No ADB-authorized Phone or Watch was available during the Personal Assistant
live test. Publishing alpha26 therefore does not claim physical alpha26 Phone
or Watch installation. Both clients require a later in-place OTA validation;
they must not be uninstalled or have application data cleared.

Google, Gmail, Calendar, Contacts, and Microsoft remain **Setup Required**
until configured and independently verified healthy.
