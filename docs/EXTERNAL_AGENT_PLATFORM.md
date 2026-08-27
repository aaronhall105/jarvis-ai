# External agent platform

Jarvis Core exposes external systems only through the provider-neutral
`ConnectorRegistry`. Installed capability definitions are setup metadata; a
capability becomes executable only when its provider reports configured,
authenticated, healthy, correctly scoped runtime state.

## Configuration

The current production adapters are:

- `homeassistant`: existing grounded read/control/admin operations.
- `openai_web_search`: live Responses API web search using
  `OPENAI_API_KEY` and `JARVIS_WEB_SEARCH_MODEL`.
- `public_web_fetch`: bounded public HTTP(S) text retrieval. Private, local,
  link-local, CGNAT, site-local, reserved, mapped-private and
  credential-bearing URLs are rejected. All DNS answers are validated, DNS and
  connection work share deadlines, the selected public address is pinned at
  socket-connect time while preserving Host/TLS SNI, redirects are checked per
  hop, and a real harmless fetch probe controls provider health.

Set `JARVIS_INTEGRATIONS_ADMIN_TOKEN` to enable the protected integrations,
receipt, plan and monitor administration endpoints. Secret values belong in
the process environment or read-only mounted secret files; provider status and
receipts never return them.

Browser, Gmail, calendar, contacts, communications, Instagram, Facebook,
TikTok, X, travel, shopping and dating services are setup-only descriptors
until both a real adapter and authorised account are configured. Their
provider-specific potential operations are visible to a settings UI but their
executable capability lists remain empty.

## API contracts

Protected endpoints use the `X-Jarvis-Integrations-Token` header:

- `GET /api/integrations/providers`
- `GET /api/integrations/capabilities`
- `GET /api/integrations/health`
- `GET /api/integrations/actions`
- `POST|GET /api/agent/plans`
- `GET|POST /api/agent/plans/{plan_id}/...`
- `POST|GET /api/external-monitors`
- `POST /api/external-monitors/{job_id}/cancel`

`/api/system/status` and `/health/ready` contain a redacted summary suitable
for operational health checks. Optional unconfigured providers do not make Core
unready.

## Execution guarantees

- Model-selected capabilities are revalidated against live registry state.
- External writes are rejected unless a durable action receipt can be committed
  before the provider call.
- Writes are never retried automatically by the connector registry.
- A verified write and an accepted-but-unverified write are different terminal
  states. Only the former is reported as successful.
- Planner steps persist status, dependencies, confirmation, attempts, results,
  failures, continuation and action receipts.
- A dependent step may consume a bounded JSON value from a successful ancestor
  with `{"$from_step":"research","path":"sources.0.title"}`. References are
  data-only, must target an ancestor, and fail before provider execution when
  evidence is absent; arbitrary expressions and code are not supported.
- Every write plan step requires verified evidence; provider acceptance alone
  leaves its outcome unresolved and cannot complete the goal.
- After a restart, an interrupted write is reconciled only through its existing
  idempotent durable action receipt. The provider is never called again merely
  because plan persistence was interrupted.
- Explicit replanning may replace only unstarted work. Completed steps and their
  evidence must remain structurally unchanged in the replacement graph, while
  running, failed, cancelled and outcome-unknown steps make automatic replanning
  fail closed.
- Plan inputs containing credential material are refused, and provider results
  are defensively redacted before persistence.
- Independent reads may run concurrently; writes are serialized.
- External monitors capture a real baseline, re-run an available read-only
  capability only when its registry metadata explicitly declares repeatability,
  stable value selectors, a minimum interval, a maximum poll count and expiry.
  They retry within bounds and deliver once to the originating chat.
- Web text baselines are stored as content fingerprints, not full page/search
  bodies. Monitor delivery text is generated from the code-owned comparison;
  model-authored wording cannot turn an unrelated page change into a factual
  price or stock claim.
- Monitor idempotency is conversation/principal scoped, replays are validated
  before a second provider call, and list/cancel operations are constrained to
  the same conversation for both text and voice tool paths.
- Deep research deduplicates canonical URLs, reports distinct source origins,
  and uses strict structured analysis over supplied excerpts to detect only
  source-bound conflicts. A hostname count is never labelled independent
  publisher evidence.

OAuth setup and provider-specific credentials are intentionally not simulated.
Adding a provider requires a real `Connector`, capability metadata, status and
health implementation, execution adapter, verification behavior and tests.
