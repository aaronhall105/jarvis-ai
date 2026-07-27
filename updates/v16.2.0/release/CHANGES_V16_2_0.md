# Jarvis v16.2.0 Changes

- Adds persistent edge-triggered conditional rules.
- Supports exact Home Assistant state, presence and numeric-threshold triggers.
- Adds one-shot and persistent rules, cooldowns, debounce and local time windows.
- Adds one-off clock checks such as “at 10 pm, turn the TV off only if it is still on”.
- Reuses the verified v16 action resolver for lights, switches, TV power and media shortcuts.
- Supports owner notifications as a conditional action.
- Adds pause, resume, cancel, cooldown editing, time-window editing and run history.
- Prevents immediate execution when a rule is created or Jarvis restarts by storing an exact baseline state.
- Adds `/api/conditions` status, rule, run-history and administrative process endpoints.
- Keeps Home Assistant Assist integration v1.5.4 unchanged.
