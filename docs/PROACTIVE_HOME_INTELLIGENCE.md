# Proactive home intelligence

Alpha36 evolves the existing Home Assistant awareness and proactive engine; it
does not add a second notification system or autonomous home control.

## Grounded device availability

Home Assistant entities are grouped only through the authoritative entity to
`device_id` registry relationship. Entities without a device link remain
separate. A physical device is unavailable only when its primary device surface
is unavailable. A failed diagnostic child while the primary surface is online
is recorded as partial availability, not as an offline physical device.

`GroundedDeviceStatus` retains the member entities, area, observation time and
evidence source. Whole-home answers present the physical device while keeping
the underlying entity evidence available for diagnostics.

## Evidence-aware document selection

Document questions remain scoped to the already-grounded Gmail thread or
Outlook conversation. Selection ranks semantic relevance, readable attachment
evidence, grounded facts in the message body, current-period recency and the
current message. Newest-message order alone is not authoritative. Provider IDs
remain isolated, candidates are bounded, and extraction keeps the existing
principal-scoped cache, size limits and currency-provenance rules.

## Proactive event lifecycle

The active pipeline is:

1. grounded Home Assistant observations;
2. physical-device normalization;
3. deterministic relevance and persistence filtering;
4. durable condition and incident deduplication;
5. contextual notification policy, quiet hours and rate limits;
6. delivery with accepted, failed or outcome-unknown receipts.

Durable conditions use `observed`, `qualified`, `suppressed` and `recovered`
states. Suppression reasons include persistence pending, transient recovery,
policy, quiet hours, cooldown and rate limiting. A restart preserves the first
observation and a previously qualified condition does not notify again merely
because Core restarted.

Recovery is eligible only when the original condition was surfaced and the
recipient's existing event-class policy permits a recovery notification.
Outcome-unknown deliveries are not blindly retried.

## Significance and notification safety

Routine state chatter is filtered deterministically and never invokes an LLM
per observation. Structured decisions retain base importance, persistence,
novelty, recurrence, user relevance and cooldown components. The existing
principal-scoped preferences, notification modes, quiet hours and channel
configuration remain authoritative.

The proactive engine may observe, summarize, notify, explain and suggest. A
proactive observation never grants Home Assistant write authority. Existing
explicit action authorization remains separate.

## Follow-up evidence

Notifications retain their grounded subject, transition, persistence and
member-entity evidence. “Why did you tell me that?” uses the persisted event.
Current-state questions re-read grounded Home Assistant state. “Anything I need
to know?” summarizes only current, previously surfaced significant incidents;
it does not dump the entire home snapshot.
