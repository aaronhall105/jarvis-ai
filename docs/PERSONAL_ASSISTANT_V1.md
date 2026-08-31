# Personal Assistant v1

Personal Assistant v1 extends Jarvis Core's existing conversation, memory,
follow-up, capability, receipt, and notification systems. It does not introduce
a second scheduler or memory store. Conversation commands are the primary user
interface in this phase.

## Reminders and recurring work

Jarvis can create one-time reminders from relative durations, today/tomorrow,
named weekdays, absolute dates, clock times, and dayparts such as morning,
afternoon, and evening. Interpretation uses the authenticated principal's or
request's IANA timezone. Jarvis resolves and stores an exact UTC timestamp
before acknowledging the reminder.

Recurring reminders use a structured schedule. Supported v1 schedules include
fixed minute/hour intervals, weekdays, named weekdays, and a day of each month.
The persisted schedule includes its timezone and next occurrence; execution
does not depend on asking the model to reinterpret the original sentence.

Examples:

- `Remind me in 45 minutes to call Mum.`
- `On Friday at 5pm remind me to phone the garage.`
- `Every weekday at 7am remind me to take my lunch.`
- `Every month on the 1st remind me to pay the rent.`

## Condition watches

Jarvis can durably watch verified Home Assistant entity states, including a
device finishing, a person arriving home, a device returning online, a person
detection sensor becoming active, or an entity changing from its captured
baseline. Entity resolution uses live Home Assistant evidence and refuses
ambiguous or unavailable targets.

Periodic `check whether … changed` requests are also stored as capability-backed
Home Assistant monitors. Unchanged observations update diagnostics without
creating user messages or invoking model reasoning. Provider-backed monitors
are accepted only when the required capability is currently available, and the
capability is checked again during execution.

## Durable lifecycle and delivery

Personal work follows one lifecycle in the existing follow-up database:

```text
resolve → validate → persist → verify persistence → acknowledge
        → execute → verify → receipt/evidence
        → originating conversation → optional notification → complete
```

Jobs are principal scoped and retain their originating conversation, device,
endpoint, capability, schedule, evaluation state, observed state, delivery
state, and audit history. Completion is appended idempotently to the same
durable conversation. A mobile notification is supplemental and is never the
authoritative completion record.

Execution and delivery fences recover after Core restart. Recurring occurrence
keys prevent duplicate conversation delivery. If a notification transport
outcome is unknowable across a crash, Jarvis records `outcome_unknown` rather
than resending blindly or claiming verified delivery.

## Managing work

Conversation commands support listing current work and recent completions,
cancelling, pausing, resuming, and rescheduling. References are resolved within
the authenticated principal and current conversation. Jarvis asks for
clarification when more than one job is plausible.

Authenticated mobile APIs are available for a future client task page:

- `GET /api/personal-assistant/jobs`
- `GET /api/personal-assistant/jobs/{job_id}`
- `GET /api/personal-assistant/jobs/completions`
- `GET /api/personal-assistant/jobs/diagnostics`
- `POST /api/personal-assistant/jobs/{job_id}/cancel`
- `POST /api/personal-assistant/jobs/{job_id}/pause`
- `POST /api/personal-assistant/jobs/{job_id}/resume`
- `POST /api/personal-assistant/jobs/{job_id}/reschedule`

The APIs use the existing mobile bearer authentication and configured owner
principal. Another principal's jobs are not returned or mutated.

## Explicit personal memory

The existing memory engine handles explicit commands such as `Remember …`,
`What do you remember about me?`, and `Forget …`. Explicit memories are
principal scoped, timestamped, durable, private, and source-aware. Corrections
to a stable preference update the current value instead of leaving conflicting
current facts. Relevant prompt context remains bounded; Jarvis does not copy the
whole memory database into every turn.

Jarvis acknowledges a save or deletion only after the existing store commits
and verifies it. If no matching personal knowledge exists, it says so rather
than constructing a profile from model output.

## Capability and failure behavior

Scheduled is not executed, execution is not verification, monitoring is not a
condition match, and notification acceptance is not confirmed delivery. Jarvis
does not promise a task when persistence or capability validation fails.

Google, Gmail, Calendar, Contacts, and Microsoft remain **Setup Required**
until configured and healthy. Personal Assistant v1 is ready to route their
future work through the same durable lifecycle, but does not add a parallel
provider scheduler or change OAuth configuration.
