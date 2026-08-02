# Jarvis v18.1.0 — Android Default Assistant and Compact Overlay

## Added

- A full Android `VoiceInteractionService`, allowing Jarvis to appear in the system Digital assistant app list.
- A `VoiceInteractionSessionService` and compact white monochrome assistant overlay shown above the current app.
- Immediate voice start when the Side button invokes Jarvis, controlled by a setting.
- Text entry, live status, streamed response text, voice start/stop, close and Open chat controls inside the overlay.
- Android Assistant role request from Jarvis Settings.
- A system-held wake-phrase host inside the selected `VoiceInteractionService`.
- Automatic return to wake-phrase listening after the assistant overlay is dismissed.
- A delegated on-device `RecognitionService`, required by Android's assistant role.
- Battery optimisation shortcut and assistant-status display in Settings.

## Preserved

- Clean white chat UI, separate settings page, persistent history and streaming replies.
- Live and Standard voice modes.
- No fixed 45-second conversation timeout.
- Realtime voices and the original Home Assistant Jarvis voice.
- Jarvis Core memory, people, presence, house awareness, devices, routines, schedules and conditional actions.
- No Home Assistant integration changes.

## Wake-word limitation

The wake phrase is continually rearmed while Jarvis is the selected assistant, but it uses Android's on-device speech recogniser rather than a dedicated low-power DSP keyword model. Samsung battery policy and recogniser availability can still affect screen-off reliability. Set Jarvis battery usage to Unrestricted for the best result.
