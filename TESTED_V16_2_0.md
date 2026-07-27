# Jarvis v16.2.0 Test Record

Validated before packaging:

- Python compilation for Core v16.2.0 source and installer patch tooling.
- 27 deterministic conditional-action tests.
- State-edge baseline and no-immediate-trigger behaviour.
- Numeric above/below threshold crossing.
- Presence arrival and departure.
- Debounce and cooldown behaviour.
- Cross-midnight and restricted time windows.
- One-shot completion and persistent rule behaviour.
- Restart persistence and interrupted-run recovery.
- Owner-scoped rule management and history.
- Action failure recording and owner failure notification.
- Package manifest and required-file integrity.
- Full staged main/task version and route marker checks.

The Ubuntu installer additionally runs the 80 relevant dependency-free Core regression tests before Docker is rebuilt.
