# Astra Executive Agent v1

Jarvis keeps deterministic, latency-sensitive commands on its existing path and
uses `gpt-6-astra` only for grounded multi-domain or multi-step work. Realtime
voice remains the audio transport; Astra receives semantic turns, not microphone
audio.

## Configuration

- `JARVIS_MODEL_ROUTER_ENABLED=true`
- `JARVIS_EXECUTIVE_ENABLED=true`
- `JARVIS_EXECUTIVE_MODEL=gpt-6-astra`
- `JARVIS_EXECUTIVE_REASONING=medium`
- `JARVIS_EXECUTIVE_MAX_REASONING=high`
- `JARVIS_EXECUTIVE_TIMEOUT_SECONDS=90`
- `JARVIS_EXECUTIVE_WEBSOCKET_ENABLED=true`

`OPENAI_MODEL` remains the standard fast-path model. Missing executive settings
use the values above; an unavailable executive model falls back without changing
the authority or confirmation boundary.

## Safety boundary

Astra receives only the principal-scoped durable planner tool. Plans may name
only capabilities currently registered as executable. The existing planner owns
confirmation, idempotency keys, action receipts, verification, recovery and
write reconciliation. Email, webpages, calendar text and other provider results
are untrusted data and cannot grant authority.

## Steering and cancellation

Responses WebSocket steering sends `response.steer` against the active response.
Acceptance is provisional; Jarvis records success only after the successor
`response.created`. A `response.steer.pending` continuation reuses saved tool
results and never reruns the tool.

Planner cancellation and supersession requests are stored durably and applied at
safe batch boundaries. Completed effects remain recorded, outcome-unknown writes
must reconcile, and pending writes that were cancelled or superseded are not
started. Core restart resumes only a committed durable plan; it never replays an
orphaned model call.

## Status

Authenticated internal status is available from `/api/executive/status` and
`/api/executive/tasks/{task_id}`. Diagnostics contain routing reason codes,
models, reasoning level, aggregate token counts, tool/model rounds, fallbacks,
failures and latency. They do not contain hidden reasoning or credentials.
