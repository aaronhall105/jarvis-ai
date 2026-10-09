# Alpha37 shared HomeExperience architecture

## Reused production foundations

Alpha37 starts from production commit
`9aecfc0ce6dfc2fc3bea8cc696f8cabf7518f338` and reuses these authorities:

| Concern | Existing authority reused |
| --- | --- |
| Grounded whole-home truth | `HomeSnapshot`, `GroundedHomeEntity`, `GroundedEntitySet`, and `HomeQueryPlan` in `bridge/app/home_intelligence.py` |
| Physical devices | `roll_up_physical_devices`; authoritative HA `device_id` grouping and primary-versus-diagnostic availability |
| Areas and rooms | `HomeRegistryCache` area/entity/device topology in `bridge/app/registry.py` |
| Live state | `ToolEngine.readable_entity_states(refresh=True)` through the existing HA client and registry cache |
| Presence | Grounded `person` state plus the existing presence service; no precise location is projected |
| Proactive history | SQLite-backed `ProactiveIntelligenceEngine` events, incidents, conditions, delivery evidence, dedupe, and recovery |
| Conversation continuity | Principal/conversation-scoped `WorkingContext` and grounded entity-set references |
| Writes | Existing external-agent `homeassistant.control` capability with verified receipts |
| Android networking | `CoreEndpointManager`, bearer authentication, endpoint failover, and Task Centre foreground/cache lifecycle patterns |
| Home Assistant | Existing `jarvis_core_conversation` custom integration and its config entry |

No version-controlled Lovelace configuration existed at the starting revision.
Consequently, alpha37 supplies a separate dashboard YAML and an explicit,
backup-first installation procedure; it does not overwrite a live dashboard.

## Projection and request flow

`HomeExperienceService` requests a fresh `HomeSnapshot`. The snapshot keeps the
legacy user-facing entity population used by alpha35/36 roll-up and separately
retains presentation sensors such as grounded power or appliance state. The
service then combines that snapshot with surfaced proactive events and active
incidents for the authenticated principal.

`GET /api/home` returns schema version 1 with a semantic revision and a
freshness-qualified ETag. Android and the HA coordinator render that payload;
neither client queries raw HA state to derive counts, occupancy, or incidents.
The SNAPSHOT conversation path invokes the same projector, and its deterministic
house-status response uses the exact `overall_status.headline`.

Registry topology remains on the existing bounded cache. Current HA states are
refreshed for each Core projection. Runtime metrics record
`home_snapshot_construction_ms`, `home_experience_construction_ms`, and
`home_api_response_ms`.

## Contract

The top-level model contains generation time, semantic revision, explicit
source freshness, overall status, people, rooms, lights, physical devices,
appliances, cameras, energy, active media, current incidents, surfaced recent
events, quick actions, and bounded diagnostics. Entity identifiers are retained
only where required for grounded references, safe actions, or optional
diagnostics; normal UI uses friendly registry names.

`RoomStatus` contains the HA area ID/name, occupancy state/summary/evidence,
light counts and members, cameras, physical devices, appliances, climate,
media, relevant incidents/events, and revision-bound actions.

## Occupancy semantics

- Available person detector active: `OCCUPIED`.
- Available motion or occupancy/presence evidence active without a person
  detector: `LIKELY_OCCUPIED`.
- Only inactive person/motion/camera evidence: `UNKNOWN` with “No current
  occupancy evidence”.
- No supported evidence: `UNKNOWN`.
- `CLEAR` is reserved for future policy-backed sensor fusion; alpha37 does not
  derive it from a negative camera binary sensor.

## Freshness and caching

Core distinguishes `LIVE`, `RECENT_CACHED`, `STALE`, and `UNAVAILABLE`. If HA
becomes unreachable, Core may return its last in-memory safe projection with
actions disabled. Android additionally persists only the Core presentation
payload under a normalized principal key. An offline snapshot is visibly
marked “Offline · Last updated …”; every action control is disabled. Tokens,
credentials, stream URLs, camera imagery, documents, and precise location are
never part of the cached projection.

The HA coordinator uses a 30-second conditional refresh. Coordinator failure
makes its entities unavailable, and the dashboard labels the visible copy as
last known rather than current.

## Explicit action safety

Core embeds the exact light IDs that were on in the generated view, then stores
that immutable set in a principal-scoped, revision-bound, expiring server
action registry. `POST /api/home/actions/{action_id}` rejects stale, missing,
cross-principal, unsupported, or no-longer-controllable targets. Each member is
executed through `homeassistant.control`; the aggregate reports `VERIFIED`,
`PARTIAL`, `FAILED`, or `UNKNOWN` from the actual receipts. Android never calls
HA directly and never enables an action from cached state.

## Privacy and future UI context

Camera data contains name, room, availability, recent grounded activity, and a
logical detail route only. It contains no HA URL, token, stream URI, or image.
The API remains principal-authenticated and includes stable room/area IDs so a
future client can explicitly pass visible-room context to conversation. Alpha37
does not infer implicit UI references and leaves normal `WorkingContext`
behavior unchanged.
