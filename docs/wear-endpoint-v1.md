# Jarvis Wear OS endpoint

The current Wear OS client is part of the unified `v19.0.0-alpha27` Android
project. It is a microphone, speaker, Tile, and assistant endpoint for the same
Jarvis Brain and conversation system used by the Phone.

Phone and Watch use package `com.aaron.jarvisvoice`, the same production
signing identity, and the shared `wearprotocol` module. They must be built from
one approved source revision.

## Architecture and routing

`JarvisWearActivity`, `JarvisTileService`, and the assistant entry point start
the Wear voice lifecycle through `WearVoiceService`. The controller owns
listening, processing, speaking, cancellation, timeout, and disconnect state.
Microphone and playback resources are released when they are no longer owned by
the active Watch session.

`WearChannelManager` uses the Wear OS Data Layer channel to move microphone,
control, transcript, status, and audio messages between Watch and Phone. Shared
protocol definitions live in `wearprotocol/`; do not create device-specific
copies.

The Phone bridge connects Watch turns to the authenticated Core realtime
runtime with endpoint kind `WATCH`. Core owns principal identity, conversation
context, turn admission, memory, Brain processing, and response state. Response
audio is routed to the active Watch endpoint rather than Phone playback.
Generation and turn identifiers reject stale frames after cancellation or
handover.

A Watch turn uses the same Core conversation identifier as the paired Phone
where appropriate. A later Phone turn can therefore continue the conversation
without creating a second Watch-only history.

## Build from unified-production

From the repository root:

```bash
cd android/jarvis-voice-client
ANDROID_HOME=/path/to/Android/Sdk \
ANDROID_SDK_ROOT=/path/to/Android/Sdk \
./gradlew --no-daemon \
  :wearprotocol:test \
  :app:testDebugUnitTest :wear:testDebugUnitTest \
  :app:assembleDebug :wear:assembleDebug
```

Debug APKs are generated at:

- Phone: `app/build/outputs/apk/debug/app-debug.apk`
- Watch: `wear/build/outputs/apk/debug/wear-debug.apk`

Production APKs are built and signed only by the approved tag-triggered release
workflow. Do not use debug signing to update a production-installed app.

## Pair and install for development

Pair the Watch with the Phone through its normal Wear OS companion application
and ensure Google Play services is available on both. Enable wireless debugging
on the Watch, pair ADB using the displayed pairing address/code, then connect
using the separate debugging address:

```bash
adb pair WATCH_IP:PAIRING_PORT
adb connect WATCH_IP:DEBUG_PORT
adb -s WATCH_IP:DEBUG_PORT install -r \
  wear/build/outputs/apk/debug/wear-debug.apk
```

Use the Phone's own authorized serial for a matching debug Phone build:

```bash
adb devices -l
adb -s PHONE_SERIAL install -r app/build/outputs/apk/debug/app-debug.apk
```

Never uninstall or clear Phone/Watch application data as a workaround. Both
clients must come from the same source revision and compatible signing identity.

## Tile and assistant

Add the Jarvis Tile from the Watch Tile picker. Tapping it opens the
conversation screen and requests microphone permission when needed.

For assistant launch, select Jarvis in the Watch digital-assistant settings when
the device exposes third-party assistant selection. Samsung One UI Watch
support for assigning the long-press Home shortcut varies by device/version and
must be verified physically. A supported launcher double-press shortcut may be
used when third-party long-press assignment is unavailable. Jarvis does not use
an accessibility service or private Samsung interception.

For development, the assistant role can be requested where Android exposes its
role command:

```bash
adb -s WATCH_IP:DEBUG_PORT shell cmd role add-role-holder \
  android.app.role.ASSISTANT com.aaron.jarvisvoice
```

## Physical validation checklist

- Confirm the installed Phone and Watch versions and signer match.
- Confirm existing Watch settings remain after an in-place update.
- Verify microphone and foreground-service permission flows.
- Verify Tile installation, launch, and round-screen layout.
- Verify assistant-role availability and configured hardware shortcut.
- Verify Watch microphone input, Brain response, and Watch speaker playback.
- Verify stop/interruption and stale-audio rejection.
- Verify Bluetooth/Wi-Fi Data Layer handover and bounded channel timeout.
- Start a conversation on Watch and continue it on Phone using the same Core
  conversation.
- Start a conversation on Phone and continue it on Watch where appropriate.
- Confirm endpoint routing never plays Watch-owned audio through the Phone.

Do not report Watch physical validation as passed unless these checks were
performed on the target hardware.
