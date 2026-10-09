# Jarvis 19.0.0-alpha37

Alpha37 makes alpha35/36 home intelligence visible through one Core-owned,
grounded presentation model shared by Android, Home Assistant, and house-status
conversation.

## One home model

- `HomeExperience` is a presentation projection over `HomeSnapshot`, fresh HA
  state and registry metadata, physical-device availability roll-up, and the
  durable proactive event/incident store. It is not an independent state store.
- `RoomStatus` retains area identity, cautious occupancy evidence, lights,
  cameras, devices, appliance/media/climate summaries, incidents, events, and
  exact quick-action targets.
- Current person detection proves occupied; active motion/presence is only
  likely occupied; inactive camera or motion evidence remains unknown rather
  than claiming a room is clear.
- Semantic revisions and ETags support conditional refresh without rebuilding
  HA topology on every request.

## Android Home

- Home joins Chat and Tasks in the alpha34 premium shell and becomes the
  launcher destination.
- Useful, non-empty sections cover people, rooms, lights, cameras, physical
  devices, appliances, energy, active media, and persisted important activity.
- Room, grouped-light, camera, and evidence-backed event details keep raw entity
  identifiers behind an optional diagnostics affordance.
- The principal-scoped local snapshot is marked offline/last-known and cannot
  execute state-changing actions.
- Light actions execute only through Core against the immutable set displayed
  to the user, with verified, partial, failed, and unknown outcomes.

## Home Assistant

- Jarvis Home integration v1.6.0 fetches the authenticated shared projection
  with conditional refresh and exposes presentation sensors without recreating
  Jarvis semantics from raw HA entities.
- A production-ready, monochrome Lovelace YAML is supplied for a separate
  dashboard. It is not automatically written into the live Lovelace database.
- Native camera and light cards preserve existing HA streaming/control paths;
  system and developer detail stays under More / System / Diagnostics.

## Compatibility and authority

- Core application version remains `3.7.0`.
- Realtime protocol remains `2`.
- Phone and Wear share versionCode `190390`, continuing the established +10
  alpha release sequence from alpha34 (`190360`), alpha35 (`190370`), and
  alpha36 (`190380`).
- Alpha37 adds no autonomous control authority and does not alter Smart Inbox,
  provider isolation, mail checkpoints, trash rules, or Gmail connection state.
