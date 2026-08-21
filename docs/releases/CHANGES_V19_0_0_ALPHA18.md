# Jarvis 19.0.0-alpha18

- Restores deterministic Android speech capture while assistant audio is playing.
- Treats explicit stop phrases as cancellation-only controls, never chat turns.
- Cancels Core brain, OpenAI response, and ElevenLabs streaming tasks together.
- Fences stale audio and completion events by server generation and client turn.
- Adds cancellation-path diagnostics and race regression coverage.
