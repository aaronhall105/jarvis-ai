# Validation performed for v15

## Passed

- Python compilation for `proactive_orchestrator.py` and `main_v15.py`.
- Shell syntax validation for `install_proactive_v15.sh`.
- Ten asynchronous policy tests covering:
  - Home and away washing-machine routing.
  - Critical safety routing during quiet hours.
  - Movement while nobody is home.
  - Door/opening duration and cancellation.
  - Quiet-hours arrival suppression.
  - Acknowledgement stopping escalation.
  - Spoken-number snoozing and redelivery.
  - Camera-offline duration without timer reset.
- Full test result: `10 passed`.

## Requires the live Jarvis environment

- Actual Home Assistant announcement delivery.
- Actual mobile notification acceptance.
- Real entity naming and presence matching.
- Container startup through `app.main_v15:app`.
- Long-running escalation behaviour with real events.
