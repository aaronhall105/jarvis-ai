# Smart Audio Gate v16.0.7

Smart Audio Gate is a transcript-level safety layer for Voice Preview follow-ups. It does not alter the microphone waveform and is not a replacement for the Voice PE device's built-in echo cancellation or noise removal.

## Behaviour

1. A wake-word-started request is accepted normally.
2. Jarvis only requests another turn when the answer genuinely asks a question or needs confirmation.
3. The gate stores the question type for one short-lived turn (12 seconds by default, configurable from 3 to 20 seconds).
4. The next transcript is accepted only when it resembles a confirmation, choice, concise answer or explicit new request.
5. Assistant self-echo, expired responses and likely ambient conversation are discarded locally and the microphone closes.

The gate is intentionally conservative for write actions: rejected speech never reaches Jarvis Core and therefore cannot operate a device.
