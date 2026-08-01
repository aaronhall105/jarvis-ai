# Jarvis v19.0.0-alpha11 — Smart Alerts and Wake Reliability

## Smart proactive alerts

- Battery alerts require Home Assistant `device_class: battery` and a percentage unit.
- Battery power, cycle count, voltage, health and charging diagnostics are ignored.
- Battery alerts trigger only when crossing low or critical thresholds.
- Battery notifications replace the previous battery notification instead of stacking.
- Oven helpers, reset controls, alert entities and test entities are ignored.
- Physical oven entities can be explicitly configured.
- Smoke, carbon monoxide, gas and water-leak sensors receive critical treatment.

## Controlled unsolicited speech

- Batteries never trigger unsolicited speech.
- Speech is restricted to safety events and a small useful-event whitelist.
- Quiet hours suppress noncritical speech.
- Critical smoke, carbon monoxide, gas and leak events can still be announced.
- Proactive announcements remain separate from Android conversation follow-up.

## Wake reliability

- “Hey Jarvis” becomes the reliable default phrase.
- Single-word “Jarvis” remains available as a more sensitive custom option.
- A wake detection must be followed by a separate meaningful command.
- Empty, filler and accidental wake activations are rejected silently.
- False wakes enter a cooldown before rearming.
- Partial Android recognition results can no longer open a conversation.
- Phone-speaker playback is not sent back into live Core audio.
