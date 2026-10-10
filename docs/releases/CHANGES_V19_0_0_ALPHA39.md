# Jarvis 19.0.0-alpha39

Alpha39 replaces snapshot-only room detection wording with one durable,
deterministic, evidence-backed occupancy state shared by Core conversation,
HomeExperience, Android, and Home Assistant.

## Room occupancy intelligence

- Adds `OCCUPIED`, `LIKELY_OCCUPIED`, `PROBABLY_CLEAR`, and `UNKNOWN` with
  confidence, freshness, source health, transition time, last strong evidence,
  clear candidate, and structured evidence.
- Classifies person/presence as strong, motion/media as supporting, and object
  detections as weak diagnostics. Animal, vehicle, phone, laptop, remote, and
  TV detections cannot establish human occupancy.
- Adds configurable hysteresis and a conservative clear interval. A person
  detector turning off never directly means the room is clear.
- Persists current state and meaningful transitions in SQLite, then reconciles
  against fresh HA state after restart so stale occupancy is not trusted.
- Processes individual HA state changes incrementally by indexed room source;
  no LLM runs per sensor event.

## Shared experience and automation output

- HomeExperience RoomStatus now carries the authoritative occupancy state and
  concise evidence while retaining full technical evidence in Diagnostics.
- Grounded room queries support occupied, clear, unknown, single-room, and
  evidence-backed follow-up responses. Unknown rooms are never listed as empty.
- Android Room Details presents the concise state and an optional evidence
  disclosure without returning to the raw sensor flood.
- Jarvis Home Assistant room sensors expose stable lowercase automation states
  and safe attributes. No binary sensor maps unknown to off.
- Ships a disabled, explicit Living Room lighting example. It turns off only
  after sustained `probably_clear`; it is never installed or enabled
  automatically and grants no general autonomous authority.

## Compatibility

- Preserves alpha38 canonical cameras/lights/devices and UX polish, alpha37
  HomeExperience/ETag/offline/action safety, alpha36 proactive durability,
  alpha35 grounded whole-home actions, and alpha34 Chat/Task/voice/Wear flows.
- Phone and Wear share versionCode `190410`, continuing the established +10
  monotonic sequence from alpha38 `190400`.
