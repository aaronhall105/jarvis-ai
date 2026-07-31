# Jarvis v19.0.0-alpha9

Alpha9 adds Core-first camera intelligence without turning the Android
communication client into a Home Assistant dashboard.

## Vision Intelligence Core

- Ingests Frigate events through the Frigate REST API or the authenticated
  `/api/vision/frigate/events` webhook.
- Stores camera, room, label, zones, confidence, snapshot availability,
  presence state, importance and duplicate-suppression state.
- Maps the initial household cameras:
  - `camera.front_door_clear`
  - `camera.hallway_clear`
  - `camera.living_room_clear`
  - `camera.bedroom_clear`
- Scores person detections more highly when Aaron and Amber are away.
- Suppresses repeated events from the same camera, label and zone.
- Monitors mapped camera entities for offline and restored states.
- Adds useful camera events to the existing Jarvis activity feed.
- Provides authenticated event, context, snapshot and description endpoints.
- Uses OpenAI image understanding on demand for concise scene descriptions.
- Never performs face recognition or identifies a person by name.
- Gives the normal Jarvis brain verified recent-camera context for questions
  such as “What happened at the front door?”

## Android communication client

- Remains a voice/text communication interface rather than a device dashboard.
- Uses the existing Jarvis activity feed for concise camera alerts.
- Keeps live feeds, recordings and detailed controls in Home Assistant.
- Renames the activity surface to `Jarvis activity`.
- Replaces the status-bar `J` with a monochrome helmet logo.
- Uses the full Jarvis logo in the expanded foreground notification.

## Release safety

- Keeps `app.main:app` as the Docker entry point.
- Adds focused Core unit tests for event normalisation, importance,
  duplicate suppression, query routing and Home Assistant links.
- Builds and publishes `jarvis-assistant-v19.0.0-alpha9-debug`.
