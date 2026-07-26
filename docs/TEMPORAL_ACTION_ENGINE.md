# Temporal Action Engine v16.0

## Scope

v16.0 adds durable one-off future actions without weakening Jarvis's existing
control allow-list. It handles temporal commands before the proactive v15 layer
and the normal AI pipeline.

## Supported actions

- Turn all lights in one exact Home Assistant area on or off.
- Turn one exact exposed light or switch on or off.
- Turn the configured living-room TV on or off.
- Open Netflix, YouTube, BBC iPlayer or Prime Video through existing allow-listed shortcuts.

Targets are resolved when the task is created and stored as exact IDs or shortcut
keys. Execution never performs fuzzy device selection.

## Task states

- `pending`
- `executing`
- `completed`
- `cancelled`
- `failed`
- `expired` (reserved for later conditional-task work)

## Persistence

State is stored in `/app/data/jarvis_tasks.db`. Tasks in `executing` state during
an unexpected restart are safely returned to `pending` on startup.

## Privacy

Voice-created tasks are owned by the authenticated Home Assistant user. Natural
listing and cancellation commands are restricted to that owner. Administrative
REST cancellation remains protected by the existing Jarvis admin token.

## API

- `GET /api/tasks/status`
- `GET /api/tasks`
- `GET /api/tasks/{id}`
- `POST /api/tasks/process`
- `POST /api/tasks/{id}/cancel`

Write endpoints use `X-Jarvis-Admin-Token`.
