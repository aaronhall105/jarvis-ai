# Conditional Actions v16.2.0

Jarvis can now create durable rules that run when an exact Home Assistant entity crosses a state boundary.

## Examples

- `When Amber leaves home, then turn the hallway light off.`
- `When the front door opens after 11 pm, then notify me.`
- `When Aaron Phone Battery drops below 20%, then notify me.`
- `Next time the washing machine status finishes, then notify me.`
- `At 10 pm, turn the TV off only if it is still on.`
- `When the front door opens for 10 seconds, then notify me with a 5 minute cooldown.`

## Safety model

Rules store the exact trigger entity ID and exact action payload at creation time. The first observed state is stored as a baseline, so creating a rule or restarting Jarvis cannot immediately execute an already-true condition. Control actions use the same verified allow-list as one-off and recurring tasks.

## Rule management

- `Show my rules.`
- `Show rule 2.`
- `Show history for rule 2.`
- `Pause rule 2.`
- `Resume rule 2.`
- `Cancel rule 2.`
- `Change rule 2 cooldown to 15 minutes.`
- `Change rule 2 time window to between 10 pm and 7 am.`

## Persistence

Rules and execution history are stored in `/app/data/jarvis_conditional_actions.db`.
