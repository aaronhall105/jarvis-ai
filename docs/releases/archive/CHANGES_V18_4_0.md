# Jarvis Android Assistant v18.4.0 — Stability Release

- Keeps the interface strictly white, black and grey.
- Uses a smaller transparent, centred adaptive launcher foreground.
- Keeps one authoritative wake-word host when Jarvis is the default assistant.
- Adds wake-word health checks, bounded retries and automatic rearming.
- Recognises both “Jarvis” and “Hey Jarvis” with a more tolerant local detector.
- Prevents the compact overlay opening while the full app is visible.
- Hands off from overlay to full chat without displaying both at once.
- Keeps Jarvis on the realtime Marin speech path.
- Adds Android speech fallback when a spoken response receives no realtime audio.
- Delays microphone reopening until the final PCM buffer has drained.
- Preserves Aaron and Amber phone identities.
- Preserves automatic close-and-stop phrases such as thanks and goodbye.
