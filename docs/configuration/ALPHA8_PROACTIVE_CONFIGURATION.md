# Alpha8 proactive configuration

Alpha8 uses the Home Assistant URL and token already configured for Jarvis.

```env
JARVIS_PROACTIVE_ENABLED=true
JARVIS_PROACTIVE_MIN_IMPORTANCE=80
JARVIS_PROACTIVE_COOLDOWN_SECONDS=300
JARVIS_PROACTIVE_POLL_SECONDS=15
JARVIS_PROACTIVE_DOOR_OPEN_SECONDS=600
JARVIS_PROACTIVE_OVEN_ON_SECONDS=1800
JARVIS_PROACTIVE_HIGH_POWER_W=3000

JARVIS_PROACTIVE_NOTIFY_AARON=notify.mobile_app_aaron_s_phone
JARVIS_PROACTIVE_NOTIFY_AMBER=notify.mobile_app_amber_phone

# Optional; leave blank until the exact Assist Satellite entity is known.
JARVIS_PROACTIVE_SPEAKER_ENTITY=
```

Locks, alarm panels, covers and sirens are blocked. Explicit Turn off actions
are limited to lights, switches, fans and media players.
