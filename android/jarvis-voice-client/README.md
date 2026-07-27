# Jarvis Voice Client

Standalone Android client for Jarvis v17.1.0.

## Build

From this directory with Android SDK 36 and Gradle 9.4.1:

```bash
gradle :app:testDebugUnitTest :app:assembleDebug
```

The debug APK is written to `app/build/outputs/apk/debug/app-debug.apk`.

## First setup

1. Create a long-lived access token in the Home Assistant user profile.
2. Enter the Home Assistant base URL and token in the app.
3. Leave Pipeline ID blank to use Home Assistant's preferred pipeline.
4. Press **Start Jarvis Voice** and grant microphone/notification permissions.

The app does not include or transmit credentials anywhere except directly to the configured Home Assistant WebSocket endpoint.
