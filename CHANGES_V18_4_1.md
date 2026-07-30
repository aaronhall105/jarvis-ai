# Jarvis Android v18.4.1

Reliability correction focused on the reported production regressions.

- Uses VoiceService as the single wake-word microphone owner.
- Keeps wake detection running in a microphone foreground service off-screen.
- The selected Android VoiceInteractionService starts and rearms that service.
- Makes Standard conversation mode the recommended and migrated default.
- Keeps Live mode available as an experimental option.
- Detects polite closing phrases including "Okay goodbye" and "Thanks Jarvis".
- Stops follow-up listening after the final closing response.
- Automatically restarts Standard recognition after recoverable timeouts.
- Preserves the monochrome UI, identity separation and current launcher assets.
