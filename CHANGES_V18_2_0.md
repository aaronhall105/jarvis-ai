# Jarvis Android Assistant v18.2.0

## User interface
- Rebuilt the main chat screen with a clean monochrome design.
- Replaced oversized text controls with compact icon buttons.
- Added a microphone button directly beside the send button.
- Added proper Android 15/16 status bar, navigation bar and keyboard insets.
- Kept the composer visible above Samsung navigation and keyboard areas.
- Changed the keyboard action to Send instead of inserting a new line.
- Updated the assistant overlay to use the same compact mic/send composer.
- Added a new adaptive monochrome Jarvis app icon.

## Voice and wake word
- Added a dedicated foreground-service action for arming the wake word.
- Kept microphone foreground-service status while local wake-word listening is active.
- Re-armed wake listening after settings changes and assistant overlay dismissal.
- Improved SpeechRecognizer recovery after busy, client and server errors.
- Added clearer permission and connection status reporting.
- Preserved the system VoiceInteractionService path when Jarvis is the default assistant.

## Build
- Version code: 18200
- Version name: 18.2.0
- GitHub Actions artifact: jarvis-assistant-v18.2.0-debug
