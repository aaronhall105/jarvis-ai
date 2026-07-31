# Alpha9 Vision Intelligence configuration

Add only the settings needed for the Frigate installation to `~/jarvis/.env`.

```env
JARVIS_VISION_ENABLED=true
JARVIS_FRIGATE_URL=http://FRIGATE-IP-OR-HOST:5000
JARVIS_FRIGATE_TOKEN=
JARVIS_VISION_MODEL=gpt-5-mini
JARVIS_VISION_POLL_SECONDS=10
JARVIS_VISION_DUPLICATE_SECONDS=120
JARVIS_VISION_RETENTION_DAYS=30
JARVIS_VISION_CAMERA_OFFLINE_SECONDS=120
JARVIS_VISION_AUTO_DESCRIBE_QUERIES=true
JARVIS_VISION_LABELS=person,package,car,truck,motorcycle,bicycle
```

The built-in camera map is:

```json
{
  "front_door": {
    "area": "Front Door",
    "entity_id": "camera.front_door_clear"
  },
  "hallway": {
    "area": "Hallway",
    "entity_id": "camera.hallway_clear"
  },
  "living_room": {
    "area": "Living Room",
    "entity_id": "camera.living_room_clear"
  },
  "bedroom": {
    "area": "Bedroom",
    "entity_id": "camera.bedroom_clear"
  }
}
```

Override it with one JSON line only when Frigate camera names differ:

```env
JARVIS_VISION_CAMERA_MAP={"front":{"area":"Front Door","entity_id":"camera.front_door_clear"}}
```

Home Assistant remains the authority for live camera feeds, recordings,
entities and detailed controls. The Jarvis app receives concise conversational
results and activity alerts only.
