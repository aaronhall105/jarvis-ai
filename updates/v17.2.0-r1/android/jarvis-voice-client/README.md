# Jarvis Realtime Voice v17.2.0

Android full-duplex voice client for Aaron's private Jarvis Core.

## What changed

- Streams 24 kHz mono PCM audio continuously to Jarvis Core.
- Uses OpenAI Realtime semantic turn detection instead of Android `SpeechRecognizer`.
- Plays streamed PCM replies with low buffering.
- Uses Android voice-communication capture, acoustic echo cancellation, noise suppression and automatic gain control when the device supports them.
- Flushes playback immediately when server-side speech detection accepts an interruption.
- Routes private home actions through the existing verified Jarvis Core tool path.

## First setup

1. Install Jarvis Core v17.2.0 on the Jarvis server.
2. Enter the Jarvis Core URL, normally `http://192.168.1.40:8000` at home or the server's Tailscale URL away from home.
3. Enter the mobile voice token printed by the v17.2.0 installer.
4. Tap **Start Live Voice** and grant microphone and notification permissions.

The OpenAI API key remains on Jarvis Core and is never stored in the APK.

## Build

With Android SDK 36 and Gradle 9.4.1:

```bash
gradle --no-daemon :app:testDebugUnitTest :app:assembleDebug
```

The debug APK is written to `app/build/outputs/apk/debug/app-debug.apk`.
