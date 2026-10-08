# Jarvis 19.0.0-alpha36

Alpha36 turns alpha35's grounded whole-home model into a quiet, durable
proactive intelligence substrate without granting autonomous home-control
authority.

## Grounded availability

- Home Assistant entities roll up through authoritative `device_id` links, so
  one camera with several unavailable children is presented as one device.
- An online primary device with an unavailable diagnostic child is reported as
  partial evidence, not a physically offline device.
- Entity-level evidence remains attached for diagnostics and follow-up.

## Grounded document selection

- Document questions search only the already-grounded provider thread or
  conversation and prefer the message or attachment that supplies the fact.
- A newer empty reply no longer outranks a relevant readable attachment.
- Candidate traversal remains bounded, provider-isolated, principal-scoped and
  subject to the established size, extraction and currency-provenance rules.

## Proactive intelligence

- Raw observations are normalized and deterministically filtered before any
  notification decision; routine state chatter makes no per-observation LLM
  call.
- Durable conditions preserve persistence timers across restart and suppress
  transient outages, duplicates and repeated recoveries.
- Existing importance modes, principal preferences, quiet hours, category
  controls, cooldowns, global rate protection and notification channels remain
  authoritative.
- Delivery distinguishes accepted, failed and outcome-unknown results; unknown
  outcomes are not blindly retried.
- Persisted evidence grounds “Why did you tell me that?”, live Home Assistant
  state grounds recovery questions, and the concise home brief includes only
  current surfaced incidents.

Alpha36 does not introduce autonomous device control, proactive mailbox writes,
or automatic integration reconnection.

## Compatibility

- Core application version remains `3.7.0`.
- Realtime protocol remains `2`.
- Phone and Wear share versionCode `190380`.
- Android alpha34/alpha35 shell, input, Task Centre, endpoint, whole-home and
  grounded-measurement behavior remains unchanged.
