# Jarvis v19.0.0-alpha8 — Proactive Household Intelligence

Alpha8 is a combined Jarvis Core and Android release.

## Core

- Polls the existing Home Assistant API every 15 seconds.
- Stores a persistent feed in `/app/data/jarvis_proactive.db`.
- Scores events from 0–100.
- Uses minimum importance 80 and the existing 300-second cooldown.
- Supports security, cameras, appliances, energy, batteries, presence and
  system categories.
- Uses quiet hours and per-profile category controls.
- Notifies through `notify.mobile_app_aaron_s_phone` and
  `notify.mobile_app_amber_phone`.
- Supports an optional Assist Satellite announcement entity.
- Records the source entity and reason for each alert.
- Blocks locks, alarm panels, covers and sirens from proactive control.
- Restricts explicit Turn off actions to lights, switches, fans and media
  players.

## Android

- Adds a House activity button.
- Adds the activity feed and proactive settings.
- Adds Dismiss, Remind 15m, View camera and safe Turn off actions.
- Handles `jarvis://proactive` notification deep links.
- Uses LAN first and Tailscale fallback.

## Initial rules

- Door or window open for 10 minutes.
- Person detection, critical when nobody is home.
- Washing-machine, dryer and dishwasher completion.
- Oven or hob on for 30 minutes.
- Battery at or below 15%.
- Power use above 3000 W.
- Aaron or Amber arriving home.
- Critical devices becoming unavailable.
