# Spoken progress acknowledgements

Jarvis Core emits progress only for real voice-pipeline requests that remain unanswered after an adaptive delay. Home Assistant Assist v1.5.1 displays the phrase as `thinking_content` and independently plays it through the media player attached to the originating Assist satellite using the default TTS engine.

The conversation request continues processing while the filler is spoken. The final answer is not added to the TTS path until the acknowledgement finishes or reaches a short safety timeout.

## Behaviour

- Typed Assist: no filler.
- Fast deterministic voice command: no filler.
- Slow state or memory request: filler after about 0.65 seconds.
- General reasoning request: filler after about 0.80 seconds.
- Frustrated correction: acknowledgement after about 0.45 seconds.

## Phrase rotation

The phrase selector has 105 phrases. It cycles through each category without repeating a phrase until the available category pool has been used.

## Home Assistant options

Open the Jarvis Core Conversation integration options to control:

- Speak progress acknowledgements
- Show progress text while working
- Follow-up mode
