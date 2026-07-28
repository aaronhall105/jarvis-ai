# Jarvis Assistant v18.1.0

A private Android chat and voice client for Aaron's Jarvis Core.

## Product UI

- Clean white monochrome chat screen with persistent history and streamed replies.
- Separate Settings page.
- Live and Standard voice modes with no fixed conversation-close timer.

## Android assistant

- Implements `VoiceInteractionService` and can be selected as the default Digital assistant app.
- Side-button invocation opens a compact overlay above the current app rather than the full Jarvis activity.
- The overlay supports listening, interruption, streamed text, typed messages, closing and opening the full chat.
- Settings can request the Android Assistant role and show whether Jarvis is active.

## Wake phrase

While Jarvis is the selected assistant, its system-held service continually rearms the configured wake phrase. The implementation uses Android's explicit on-device speech recogniser. It is not a dedicated DSP hotword model, so Samsung battery policy can affect screen-off reliability.

## Privacy

The OpenAI API key remains on Jarvis Core. Mobile and optional Home Assistant tokens are encrypted through Android Keystore.
