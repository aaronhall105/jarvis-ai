# Read-only voice speculation

Jarvis can prepare a strictly read-only tool result from a genuine incremental
Realtime transcription, then reuse it only after the completed transcript has
gone through the normal authoritative planner and produced an exact semantic
match. The feature is disabled by default with
`JARVIS_SPECULATIVE_READS_ENABLED=false`.

## Realtime signal audit

The production voice proxy uses OpenAI Realtime with `gpt-4o-transcribe`. It
already handled server VAD, final
`conversation.item.input_audio_transcription.completed` events, and optional
final token log probabilities. It did not handle the provider's genuine
`conversation.item.input_audio_transcription.delta` event.

The Realtime API documents the delta as an incremental transcription fragment
identified by `item_id` and `content_index`. Completion events remain the
authoritative transcript, and ordering across different input items is not
guaranteed. Jarvis now retains a bounded 4,096-character buffer per current
item and correlates by `item_id`. Raw partials are not logged or shown to the
user. The existing model and ASR provider are unchanged.

References:

- <https://developers.openai.com/api/docs/guides/realtime-transcription>
- <https://developers.openai.com/api/reference/resources/realtime/server-events>

## Structural safety boundary

Every known operation has deterministic effect metadata:

- `PURE_LOCAL`
- `READ_ONLY_EXTERNAL`
- `WRITE_REVERSIBLE`
- `WRITE`
- `DESTRUCTIVE`
- `UNKNOWN`

Only the first two classes can be admitted, and classification alone is not
enough. An operation also needs explicit per-tool enablement and a callback in
the separately constructed `ReadOnlyToolRegistry`. That registry has no
general connector registry, Home Assistant service executor, mail writer,
scheduler, task writer, or calendar writer. Unknown operations fail closed.

The initial executable projection is deliberately narrow:

- `get_entity_state`
- `search_entity_states`
- `list_area_states`
- `inspect_presence`

These call Home Assistant state/registry read methods only. Home controls,
parameter controls, notification/announcement operations, proactive speech
continuations, mail, calendar, task/scheduler actions, memory writes, admin
changes, and unaudited external reads are excluded. Gmail remains disconnected
and is not touched. Outlook reads are not enabled because provider mutation
semantics have not yet been certified for speculative use.

## Lifecycle

1. Realtime accumulates provider deltas by input `item_id`.
2. A short bounded debounce collapses successive deltas. At most one semantic
   planning reservation exists per voice session and utterance generation.
3. The provisional planner sees only schemas from the executable read
   projection. It may select one complete, unambiguous read or no tool.
4. A read result is cached for eight seconds and is never presented.
5. The completed transcript always enters the ordinary Core request and tool
   planner.
6. Immediately before ordinary execution, Jarvis compares task family,
   operation, exact tool, canonical JSON arguments, reference scope, provider,
   conversation, session, and utterance generation.
7. An exact fresh match reuses the normal-shaped tool result. Any mismatch,
   timeout, error, expiry, cancellation, context/generation change, or result
   still in flight falls back to ordinary execution without waiting.

Fingerprints are SHA-256 hashes of normalized semantic fields. They do not
contain raw transcript text, secrets, or provider payloads. Starting a new
speech generation invalidates and, where possible, cancels older work.

## Observability and rollout

Realtime status reports whether speculation is enabled, the genuine provider
signal, delta count, effect-class counts, eligible tools, TTL/timeout, and
bounded counters for candidates, admission, completion, rejection, mismatch,
reuse, cancellation, expiry, errors, and fallback. Timing records how long a
reused result was ready before final validation. This is not treated as a
measured live latency improvement.

Enable only for a controlled authenticated test session after deployment:

```bash
JARVIS_SPECULATIVE_READS_ENABLED=true
```

Disabling the flag restores the baseline path without rollback. Android and
Wear continue to send audio through the existing Core Realtime session; no
second ASR stream or device-side tool executor exists.

## Local validation

Core tests use inert callbacks to cover exact reuse, target/tool/generation and
scope mismatch, timeout, provider failure, TTL expiry, bounded partials, and
feature-off fallback. They also try representative Home, mail, scheduler, task,
calendar, unknown, and malicious wrapper writes and assert zero write callback
invocations. No external mutation is required.
