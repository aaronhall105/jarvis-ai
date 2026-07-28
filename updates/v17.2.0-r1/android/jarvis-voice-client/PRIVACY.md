# Privacy

Jarvis Realtime Voice is a private client for Aaron's Jarvis Core.

- Microphone audio is sent only to the configured Jarvis Core WebSocket endpoint.
- Jarvis Core relays the live audio session to the configured OpenAI Realtime API account.
- The OpenAI API key is stored only on Jarvis Core, not in the Android app.
- The mobile voice token is encrypted with Android Keystore before local storage.
- The app does not contain advertising, analytics, crash reporting or third-party tracking.
- Home Assistant device actions are executed by Jarvis Core's existing verified tool layer.

Stopping the foreground service stops microphone capture and closes the live WebSocket session.
