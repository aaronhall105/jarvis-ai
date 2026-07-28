# Privacy

Jarvis Unified Voice is a private client for Aaron's Jarvis Core.

- Live microphone audio is sent to the configured Jarvis Core WebSocket endpoint.
- Jarvis Core relays live audio to the configured OpenAI Realtime API account for transcription and optional Realtime speech output.
- Every completed request is processed by Aaron's Jarvis Core before a response is spoken.
- The OpenAI API key remains on Jarvis Core and is never stored in the APK.
- The Jarvis mobile token and optional Home Assistant token are encrypted with Android Keystore before local storage.
- When the original Jarvis voice is selected, response text is sent to the configured Home Assistant Assist TTS pipeline and the returned TTS media is played by the phone.
- Sleeping wake-phrase recognition uses Android's on-device recogniser when available. If on-device recognition is unavailable, Android may use the system recognition provider.
- The app contains no advertising, analytics, crash reporting or third-party tracking.

Stopping the foreground service stops microphone capture, wake-phrase recognition and all live connections.
