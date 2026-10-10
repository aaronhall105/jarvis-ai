# Alpha39 room occupancy architecture

Jarvis Core owns one deterministic `RoomOccupancyEngine`. It consumes the same
registry-enriched Home Assistant state as `HomeSnapshot`, persists derived room
state and meaningful transitions, and supplies `HomeExperience`, conversation,
Android, and the Jarvis Home Assistant integration. Clients do not reconstruct
occupancy.

## States and evidence

- `OCCUPIED`: fresh strong evidence is active, such as a person detector or an
  explicit room-presence/mmWave sensor.
- `LIKELY_OCCUPIED`: strong evidence recently disappeared, or supporting motion
  or media activity extends a recent strong observation.
- `PROBABLY_CLEAR`: reliable strong sources remain available and inactive for
  the configured conservative clear interval.
- `UNKNOWN`: sources are missing, stale, unavailable, contradictory, or not
  sufficient to justify either occupied or clear.

Person/presence signals are strong. Motion and media are supporting and cannot
establish human occupancy from an unknown baseline. Animal, vehicle, phone,
laptop, remote, TV, and generic object detections are weak diagnostics; they do
not establish human occupancy.

The default policy uses five minutes of strong-evidence persistence, two
minutes of motion support, five minutes of clear confirmation, and a two-minute
disconnect grace period. All values are configurable through the
`JARVIS_OCCUPANCY_*_SECONDS` settings. There are no per-room entity IDs in Core.

## Transitions, freshness, and durability

Strong evidence enters `OCCUPIED` immediately. Losing it moves an occupied room
to `LIKELY_OCCUPIED`, never directly to clear. `PROBABLY_CLEAR` requires the
full clear interval and healthy sources. `UNKNOWN` is never equivalent to off
or empty.

Current derived state, transition time, last strong evidence, clear candidate,
source health, and evidence timestamps are stored in
`jarvis_room_occupancy.db`. Core reconciles that state with a fresh HA snapshot
at startup; stale stored occupancy is not trusted. Only derived state changes
enter the transition timeline, not every raw detector twitch.

## Automation consumption

The Jarvis HA room sensor exposes stable lowercase states (`occupied`,
`likely_occupied`, `probably_clear`, `unknown`) plus confidence, freshness,
source health, last strong evidence, and evidence count. Automations may use
`occupied` to request on and a sustained `probably_clear` to request off.
`likely_occupied` and `unknown` must never request off.

The opt-in Living Room example is
`home_assistant/config/alpha39_living_room_occupancy_automation.yaml`. It is
shipped disabled and is never installed automatically. Review entity IDs and
manual-override expectations before explicitly importing and enabling it.
Alpha39 does not add a general autonomous-control authority or a manual
override policy; that remains future policy work.

Future mmWave, BLE, anonymous counts, and identified-person evidence can add
new grounded evidence adapters without changing the public state machine.
