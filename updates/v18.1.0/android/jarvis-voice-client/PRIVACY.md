# Privacy

Jarvis Assistant is a private client for Aaron's Jarvis Core.

- Chat and microphone data are sent to the configured private Jarvis Core endpoint.
- Jarvis Core relays live audio to the configured OpenAI API account for transcription and optional speech rendering.
- Every completed request is processed by Jarvis Core before a final response is displayed or spoken.
- The OpenAI API key is never stored in the APK.
- Mobile and Home Assistant tokens are encrypted with Android Keystore.
- When Jarvis is selected as the Android assistant, Android keeps its `VoiceInteractionService` available. Wake-phrase audio is handled by Android's on-device recogniser when available.
- The app contains no advertising, analytics or third-party tracking.
