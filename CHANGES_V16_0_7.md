# Jarvis v16.0.7 — Smart Audio Gate

- Changes the legacy always-open follow-up default to Smart mode once.
- Reopens the Voice Preview microphone only when Jarvis genuinely expects a reply.
- Adds an twelve-second transcript receipt window for follow-up answers.
- Silently rejects expired follow-ups, filler-only speech, wake-word-only speech and likely assistant self-echo.
- Uses question-aware gating for confirmations, choices and missing information.
- Accepts concise expected answers and explicit new Jarvis commands.
- Rejects long unrelated background conversation before it reaches Jarvis Core.
- Logs gate reason, confidence, expected-answer type and word count locally for diagnosis.
- Preserves conversation closure, spoken progress, temporal tasks and capability grounding.
- Keeps Home Assistant config-entry version 2.
