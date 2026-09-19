# Smart Important Only notifications

Jarvis records household activity separately from deciding whether somebody should be
interrupted. A high numeric score is evidence, not notification authority.

## Policy modes

- `important_only` (production default): critical safety events, contextual away/security
  incidents, critical personal battery events, and other specifically grounded important
  conditions may interrupt.
- `all_useful`: includes useful non-routine events such as appliance completion and low
  batteries, subject to recipient settings and quiet hours.
- `critical_only`: only deterministic smoke, carbon-monoxide, gas and leak events interrupt.

Every evaluated event remains available in Jarvis activity. Each event records the policy
mode, recipient decision, reason code, quiet-hours result, incident identity and delivery
outcome. `/api/proactive/events/{id}/explain` returns that evidence; `/api/proactive/incidents`
shows durable incident lifecycle and cooldown state.

Critical smoke, carbon-monoxide, gas and leak events bypass quiet hours. Other proactive
phone interruptions do not. Disabling proactive notifications or a category remains an
explicit user preference even for critical events.

## Incident lifecycle

Jarvis groups repeated observations by immutable event kind, entity and audience. A durable
incident records first/last seen time, occurrence count, notification count, retry state,
cooldown, last event and last decision. Persistent incidents resolve only after the provider
entity returns to a safe state. A restart therefore cannot turn the same open door or camera
detection into a new notification.

## Producer audit

Core producers:

- Proactive Intelligence: contextual household activity; governed by this policy.
- Email Assistant: explicitly enabled important/reply alerts with its own durable provider
  deduplication.
- Follow-up, recurring schedule, conditional action and task engines: authenticated,
  explicitly requested reminders/monitors and their requested completion/failure notices.
- Direct notification tool: current-turn authenticated requests with principal-scoped mobile
  routing.
- Self-improvement/admin paths: operational actions controlled by their existing explicit
  configuration and administrative boundaries.

Home Assistant producers found during the 2026-09-19 production audit:

- `automation.amber_home_arrival_and_leave_alert`: direct Aaron phone pushes for Amber's
  routine presence changes (policy bypass).
- `automation.washing_machine_finished_alert_2`: Alexa announcement plus direct Aaron and
  Amber phone pushes (policy bypass).
- `automation.oven_preheat_alert`: Alexa announcement plus direct Aaron and Amber phone
  pushes (policy bypass).
- `automation.battery_low_alert` and `automation.battery_dead_alert`: direct pushes to both
  phones without ownership routing (policy bypass and duplicate of Core battery handling).
- Voice Preview offline/restored, microphone mute and daily health automations: operational
  phone pushes outside the contextual policy.
- `automation.jarvis_welcome_amber_home`: a low-priority living-room announcement, not a
  mobile notification.
- `script.jarvis_admin_test`: an explicit administrator test, retained.

The live definitions were backed up before modification under the Jarvis runtime `data/backups`
directory. Legacy phone-only bypass automations are disabled. Where an automation also has a
useful local announcement/helper action, only its mobile notification actions are removed.
Presence entities and tracking are not modified.
