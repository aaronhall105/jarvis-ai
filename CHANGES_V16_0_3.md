# Jarvis v16.0.3 — Spoken Natural Progress

## Fixed

- Progress acknowledgements are spoken while Jarvis is still processing, rather than being read only when the final answer is ready.
- Typed Assist no longer receives filler messages merely because the Companion App supplied a device ID.
- Progress remains `thinking_content`, so it does not become a second sent bubble or join the final answer.
- The final reply waits briefly for the filler phrase to finish, preventing overlapping speech.

## Improved

- More than 100 natural filler phrases across state, memory, weather, energy, control, general, playful, happy and frustrated contexts.
- Recently used phrases are excluded from selection to prevent obvious repetition.
- Filler starts after roughly 0.45–0.80 seconds only when the answer is not ready.
- Fast controls, task commands, yes/no replies and typed chat skip filler.
- Home Assistant options independently control spoken progress and visible progress text.

## Included from v16.0.2

- `Delete history` asks whether completed task history was intended.
- A following yes confirms deletion; no or cancel preserves history.
- The task clarification is user-scoped, persistent and expiry-limited.
