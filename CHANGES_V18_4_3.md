# Jarvis Android v18.4.3

Self-echo and barge-in reliability correction.

- Keeps interruption active while Jarvis is thinking.
- Keeps interruption available while Jarvis is speaking.
- Rejects transcripts matching Jarvis's own current reply.
- Retains echo suppression for 2.2 seconds after playback because Android
  recognition results can arrive after the audio completion callback.
- Uses phrase containment, ordered-token matching and overlap matching rather
  than only comparing the first few words.
- Requires a stable short partial or a clear four-word partial before
  interrupting playback, while final non-echo commands still interrupt.
- Preserves Standard mode, dedicated offline wake, overlay closing and
  conversation-ending behaviour.
