# Jarvis v16.1.0 validation

Validated before packaging:

- Python compilation for the recurring schedule engine and patcher.
- 17 recurring schedule unit tests.
- Daily, weekday, weekend, named-day and interval parsing.
- Exact next-run calculation in Europe/London.
- Spring-forward nonexistent local time handling.
- Autumn clock-change duplicate-run prevention.
- Restart persistence.
- Duplicate schedule prevention.
- Per-user pause, resume and cancellation controls.
- Time edits.
- Missed-run grace and skip behaviour.
- Capability revalidation before execution.
- Per-occurrence execution history and notifications.
- Package structure and installer validation.

The installer also runs the complete existing Core regression suite on the target Jarvis PC before rebuilding Docker.

## Package revision 2

The installer runs the 53 dependency-free Core regression tests directly and does not require host-level pytest or httpx.
