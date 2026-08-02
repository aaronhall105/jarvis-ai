# Jarvis v16.1.0 changes

- Adds restart-safe recurring schedules for daily, weekday, weekend, named-day and interval actions.
- Stores exact Home Assistant action targets before a schedule is activated.
- Adds pause, resume, cancel, time-change, next-run and run-history commands.
- Handles Europe/London daylight-saving transitions using timezone-aware wall-clock calculation.
- Skips stale missed runs outside a configurable catch-up window and records the skipped occurrence.
- Re-checks device and room availability before every occurrence.
- Preserves per-user ownership for Aaron and Amber.
- Adds recurring schedule REST status, listing, history, processing and lifecycle endpoints.
- Keeps Home Assistant Assist on cumulative integration v1.5.4.
