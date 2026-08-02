# Jarvis Proactive Action Orchestrator v15

## Added

- Presence-aware routing between the living-room announcement script and Aaron's,
  Amber's or both mobile phones.
- Quiet hours with critical-alert bypass and normal-alert deferral.
- Per-alert audit records explaining the route and delivery result.
- Duplicate suppression and persistent alert state.
- Timed front-door/opening checks.
- Camera-offline duration checks.
- Movement-while-away critical alerts.
- Devices-left-on checks when the last person leaves.
- Escalation for unresolved high and critical alerts.
- Natural acknowledgements, snoozing, temporary suppression and forwarding.
- REST status, alert and audit endpoints.

## Integration

v15 wraps the existing `app.main` application through `app.main_v15`. Existing
conversation, memory, Admin Mode, House Awareness and self-improvement features
remain in place. The legacy v13 automatic announcement path is disabled at
runtime so an awareness event is delivered only once.

## Core compatibility

- Built for the `conversation-engine` branch at Jarvis Core `2.1.0`.
- FastAPI application metadata reports `2.2.0` when the v15 wrapper is active.
- Existing `/health` compatibility output remains unchanged.
