# Jarvis Assistant v18.1.0

Jarvis can now be selected as Android's default digital assistant. Holding the configured Samsung Side button opens a compact white assistant overlay above the current app, starts listening, streams Jarvis Core's answer and allows interruption or text entry without opening the full chat screen.

When Jarvis is the selected assistant, its `VoiceInteractionService` remains available and continuously rearms the configured wake phrase. The wake implementation uses Android's on-device speech recogniser; it is not a dedicated DSP keyword model.

The full clean chat UI remains available through **Open chat**, while all assistant, voice, wake, Core and Home Assistant settings remain on the separate Settings page.
