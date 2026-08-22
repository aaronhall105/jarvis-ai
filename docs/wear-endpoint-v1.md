# Jarvis Wear Endpoint v1

The v1.1 certification pass adds an inset-aware round-screen conversation UI,
streamed user/assistant transcripts, native Wear text input, and end-to-end
latency/audio diagnostics. Voice and typed turns share the active Core
conversation ID. The monochrome `J A R V I S` wordmark replaces the former
circular J artwork on phone and watch.

The Wear app is a microphone/speaker endpoint; the paired phone remains the Jarvis Core network and processing hub. Both APKs use `com.aaron.jarvisvoice` and must be signed with the same certificate so Google Play services permits their private Data Layer relationship.

## Architecture and audio routing

`JarvisWearActivity`, the Tile, and the official voice-assistant session all start one `WearVoiceService` conversation. `WearConversationController` owns the `IDLE → LISTENING → PROCESSING → SPEAKING → LISTENING` lifecycle and a configurable 60-second post-playback inactivity timeout. The recorder exists only during an active listening state and is stopped/released in `IDLE`, processing, speaking, cancellation, failure, and disconnect states.

Audio is PCM16, mono, 24 kHz, matching Alpha19's realtime pipeline. `WearChannelManager` opens `/jarvis/watch/voice/v1` with the official Wear OS `ChannelClient`. The phone's `WearVoiceBridge` feeds watch microphone frames into the existing `JarvisRealtimeClient`. `AudioEndpointRouter` explicitly binds the session to `WATCH`; realtime response frames are then sent to `WearAudioPlayer` and never to phone `AudioTrack`. Phone sessions retain the existing phone sink. Session generations scope every frame, so cancellation flushes output and rejects stale audio.

The phone authenticates Watch sessions to Core with `endpoint=WATCH` and forces the realtime `LIVE` input mode because Core intentionally ignores raw PCM in `STANDARD` mode. It preserves the phone's existing conversation ID, so follow-up turns retain context. Starting a later phone session restores the phone's configured conversation mode and `endpoint=PHONE`.

The watch X sends the existing Core turn cancellation through `VoiceService`, stops capture/playback, closes the voice session and Data Layer channel, and returns to idle. Natural closing phrases use the existing `ConversationEndPolicy` and close the same watch session. Link/Core failures display a short error and safely end the watch session.

## Install and pair

Build and install phone and watch debug APKs from the same checkout/signing configuration. Pair the Galaxy Watch with the phone in its normal Wear OS companion app and ensure Google Play services is enabled on both. The Data Layer is not available when a Wear OS watch is paired to iOS.

```bash
cd /home/aaron/jarvis-wear/android/jarvis-voice-client
ANDROID_HOME=/home/aaron/Android/Sdk \
ANDROID_SDK_ROOT=/home/aaron/Android/Sdk \
./gradlew \
  :app:assembleDebug :wear:assembleDebug
```

The APKs are:

- phone: `app/build/outputs/apk/debug/app-debug.apk`
- watch: `wear/build/outputs/apk/debug/wear-debug.apk`

Enable wireless debugging on the watch, pair ADB using the pairing address/code shown by Wear OS, and then connect using the separate debugging address:

```bash
adb pair WATCH_IP:PAIRING_PORT
adb connect WATCH_IP:DEBUG_PORT
adb -s WATCH_IP:DEBUG_PORT install -r wear/build/outputs/apk/debug/wear-debug.apk
```

Install the phone APK through the phone's own ADB serial if it is not already on the matching build:

```bash
adb devices -l
adb -s PHONE_SERIAL install -r app/build/outputs/apk/debug/app-debug.apk
```

Both APKs must come from the same checkout and signing key. Release signing uses the same `JARVIS_SIGNING_*` environment variables in both modules. The default debug builds use the same local Android debug certificate automatically.

Add the **Jarvis** Tile from the watch Tile picker. Tapping it opens the conversation screen and starts listening after microphone permission is granted.

For assistant launch, open the watch's default digital-assistant settings and select **Jarvis** if the device exposes third-party assistant selection. Jarvis declares the official `VoiceInteractionService`, session service, and `ACTION_ASSIST` entry point. Samsung One UI Watch support for assigning the long-press Home assistant shortcut varies and must be verified on the target watch. If Jarvis is not offered for long-press, assign its launcher activity to the supported Home-button double-press app shortcut. No accessibility service or Samsung-specific interception is used.

For development, the official Assistant role can also be requested over ADB where the watch build exposes Android's role command:

```bash
adb -s WATCH_IP:DEBUG_PORT shell cmd role add-role-holder \
  android.app.role.ASSISTANT com.aaron.jarvisvoice
```

The inactivity timeout defaults to 60 seconds and is bounded to 15–300 seconds. Product builds can change the `com.aaron.jarvisvoice.WATCH_INACTIVITY_TIMEOUT_MS` application metadata value without adding watch settings UI.

## Physical validation checklist

- Verify microphone permission and foreground-service prompts on the target One UI Watch version.
- Verify Tile installation/launch and round-screen layout.
- Verify Assistant-role availability, long-press behavior, and Home double-press assignment.
- Verify watch speaker routing, Bluetooth/Wi-Fi Data Layer handoff, immediate X cancellation, natural closing phrases, and 60-second timeout.
- Check basic turn-taking first; enable/assess full-duplex barge-in only if the physical watch audio stack provides reliable echo control.
