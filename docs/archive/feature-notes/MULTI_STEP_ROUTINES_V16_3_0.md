# Jarvis v16.3.0 — Multi-Step Routines and Scenes

Jarvis can now resolve and execute up to eight exact actions in sequence. Each target is resolved before the routine or rule is stored.

## Immediate scenes

- `Turn the living-room lights off, turn the TV on and open Netflix.`
- `Turn the hallway lights on and announce that Amber is home in the living room.`

Immediate scenes run once and are not saved.

## Saved routines

- `Create a routine called Movie Night: turn the living-room lights off, turn the TV on and open Netflix.`
- `Start Movie Night.`
- `Show my routines.`
- `Show routine 1.`
- `Show history for routine 1.`
- `Rename routine 1 to Cinema Mode.`
- `Disable routine 1.`
- `Enable routine 1.`
- `Delete routine 1.`

Saved routines are private to their owner and persist through restarts.

## Shared multi-step actions

The same sequence action type is available to recurring schedules and conditional rules:

- `Every night at 10 pm turn the TV off and turn the hallway lights off.`
- `When the front door opens, then turn the hallway light on and notify me that the door opened.`

A sequence stops at the first failed step. The run history records each completed and failed step.

## Supported steps

- exact light or switch on/off actions;
- TV power and configured TV app shortcuts;
- exact Home Assistant scripts and automations;
- delays from one second to five minutes;
- Aaron, Amber or both-phone notifications;
- living-room announcements.
