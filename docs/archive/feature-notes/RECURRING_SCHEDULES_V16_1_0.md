# Recurring Schedules v16.1.0

## Supported phrases

- `Every weekday at 6:30 am turn the bedroom lights on.`
- `Turn the TV off every night at 10 pm.`
- `Every Saturday at 9 am turn the living room lights on.`
- `Every Monday and Wednesday at 7 pm turn the bedside fan on.`
- `Every two hours turn the bedside fan on.`
- `Show my schedules.`
- `When will my next schedule run?`
- `Pause schedule 2.`
- `Resume schedule 2.`
- `Change schedule 2 to 11 pm.`
- `Show schedule 2 history.`
- `Cancel schedule 2.`

## Safety model

Recurring schedules use the same narrow action allow-list as one-off temporal actions: exact Home Assistant room lights, exact exposed lights or switches, configured television power and configured media shortcuts. The target is resolved when the schedule is created and is checked again before each occurrence.

## Persistence and missed runs

Schedules and occurrence history are stored in `/app/data/jarvis_recurring_schedules.db`. A due occurrence delayed by no more than five minutes runs once by default. Older missed occurrences are recorded as skipped and the next future occurrence is calculated. The grace period is configurable with `JARVIS_SCHEDULES_MISFIRE_GRACE_SECONDS`.

## Daylight saving

Wall-clock schedules are calculated in `Europe/London`. A nonexistent spring-forward time is moved to the corresponding first valid local time. An ambiguous autumn time runs only once, using the first occurrence.

## Environment settings

- `JARVIS_SCHEDULES_ENABLED=true`
- `JARVIS_SCHEDULES_POLL_SECONDS=1`
- `JARVIS_SCHEDULES_MISFIRE_GRACE_SECONDS=300`
- `JARVIS_SCHEDULES_NOTIFY_COMPLETION=true`
