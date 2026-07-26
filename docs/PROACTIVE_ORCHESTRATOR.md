# Proactive Action Orchestrator v15

## Purpose

House Awareness records verified Home Assistant state changes. The v15
orchestrator decides whether an event should be announced, sent to a phone,
held until morning, suppressed as a duplicate or escalated.

## Default policy

- Safety alerts: living-room announcement when someone is home, plus both phones.
- Movement while nobody is home: both phones.
- Washing finished: announcement when someone is home; Aaron's phone otherwise.
- Low battery: the affected person's phone when the owner can be identified.
- Arrivals: daytime announcement only.
- Door/opening left open: alert after five minutes if it is still open.
- Camera unavailable: alert after two minutes if it is still unavailable.
- Last person leaves with devices on: Aaron's phone.

High and critical alerts can escalate when they remain unacknowledged. Critical
alerts bypass quiet hours. Normal alerts are held until quiet hours end when
appropriate.

## Natural replies

After an active alert, Jarvis understands:

- `Thanks.`
- `I know.`
- `Remind me again in ten minutes.`
- `Don't tell me about that again tonight.`
- `Send that to Amber.`
- `Show active alerts.`

The reply is handled deterministically before the normal AI pipeline and is
stored in the user's existing conversation history.

## Configuration

```dotenv
JARVIS_PROACTIVE_ENABLED=true
JARVIS_PROACTIVE_TARGET=living_room
JARVIS_TIMEZONE=Europe/London
JARVIS_PROACTIVE_QUIET_START=22:30
JARVIS_PROACTIVE_QUIET_END=07:00
JARVIS_PROACTIVE_POLL_SECONDS=5
JARVIS_PROACTIVE_COOLDOWN_SECONDS=300
JARVIS_PROACTIVE_OPENING_DELAY_SECONDS=300
JARVIS_PROACTIVE_CAMERA_OFFLINE_SECONDS=120
JARVIS_PROACTIVE_CAMERA_SCAN_SECONDS=30
JARVIS_PROACTIVE_ESCALATION_SECONDS=300
JARVIS_PROACTIVE_MAX_ESCALATIONS=2
JARVIS_PROACTIVE_PROCESS_EXISTING_EVENTS=false
```

`JARVIS_PROACTIVE_PROCESS_EXISTING_EVENTS=false` prevents old House Awareness
events being replayed on the first v15 startup. The cursor is persisted after
that, so events missed during a short restart are processed when Jarvis returns.

## Data

The orchestrator stores its state in:

`/app/data/jarvis_proactive.db`

The database contains alert records, timed conditions, suppressions and an audit
trail. It does not store Home Assistant credentials.

## API

Read endpoints:

- `GET /api/proactive/status`
- `GET /api/proactive/alerts`
- `GET /api/proactive/audit`

Write endpoints use the existing `X-Jarvis-Admin-Token` protection:

- `POST /api/proactive/process`
- `POST /api/proactive/alerts/{id}/acknowledge`
- `POST /api/proactive/alerts/{id}/snooze`

## Current audio boundary

v15 uses the configured `script.jarvis_living_room_announce` target through the
existing Tool Engine. Selecting the actual occupied room and controlling
per-room announcement volume remains the separate multiroom-audio stage.
