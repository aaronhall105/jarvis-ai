# Jarvis Proactive Orchestrator v15.0.1

This hotfix corrects alert lifecycle and false-positive behaviour found during the first live v15 run.

## Fixed

- Frigate object and generic `all occupancy` sensors are no longer treated as intrusions.
- Away occupancy is trusted only for explicit person, presence or motion entities, or entities placed in `JARVIS_PROACTIVE_SECURITY_OCCUPANCY_ENTITIES`.
- A second critical event cannot bypass active duplicate suppression.
- `occupancy_cleared` resolves the matching away-occupancy alert.
- A person arriving home resolves all outstanding `occupancy_while_away` alerts.
- Escalation verifies the live entity state and household presence before notifying again.
- One-shot arrivals and appliance notices are not counted as active alerts.
- `devices_left_on` is created once for the household, excludes Home Assistant infrastructure and does not escalate.
- `safety_cleared` resolves the matching safety alert.

## Explicit security occupancy allowlist

For a trusted sensor whose name does not contain `person`, `presence` or `motion`, add it to `.env`:

```text
JARVIS_PROACTIVE_SECURITY_OCCUPANCY_ENTITIES=binary_sensor.front_door_alarm,binary_sensor.hall_pir
```

Separate multiple entity IDs with commas, then recreate Jarvis Core.
