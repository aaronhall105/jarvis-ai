# Jarvis Android Assistant v18.3.0

## Spoken replies

- Removes the unsupported per-response Realtime audio speed field.
- Keeps the supported session-level speech configuration.
- Retains the audio format and voice override used for spoken Jarvis responses.
- Adds a regression test preventing the invalid speed field from returning.

## Dedicated wake word

- Adds optional on-device Picovoice Porcupine wake detection using the built-in JARVIS keyword.
- Adds adjustable wake sensitivity.
- Stores the Picovoice AccessKey using Android Keystore-backed encryption.
- Falls back to Android speech recognition when dedicated detection is unavailable.
- Continues hosting wake detection through Jarvis's selected VoiceInteractionService.

## Default assistant and Settings

- Adds a direct action to Android's Default apps settings.
- Shows whether Jarvis is currently the selected Android assistant.
- Reorganises Settings into cleaner monochrome cards with clearer status indicators.
- Retains safe status-bar, navigation-bar and keyboard inset handling.
