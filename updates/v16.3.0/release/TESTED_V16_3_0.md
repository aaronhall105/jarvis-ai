# Jarvis v16.3.0 validation

The staged release is validated with dependency-free `unittest` suites covering:

- capability grounding;
- spoken progress behaviour;
- one-off temporal actions;
- recurring schedules, including multi-step schedules;
- conditional actions, including multi-step rules;
- persistent named routines and immediate scenes;
- package structure and version integrity.

The installer compiles staged and installed source before rebuilding Docker. It then waits for live status endpoints to report:

- Task Engine 16.3.0;
- Recurring Schedule Engine 16.1.0;
- Conditional Action Engine 16.2.0;
- Routine Engine 16.3.0.

Live hardware validation is still required after installation.
