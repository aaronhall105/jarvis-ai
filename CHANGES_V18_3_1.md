# Jarvis Android Assistant v18.3.1

## Dedicated offline wake word

- Removed Picovoice and Porcupine completely.
- Removed the Picovoice credential field and encrypted credential storage.
- Added a fully local sherpa-onnx keyword detector for **Jarvis**.
- Bundles the official English-capable sherpa-onnx KWS model during GitHub Actions.
- Uses no cloud account and sends no wake-word audio away from the phone.
- Keeps Android speech recognition as an automatic fallback.
- Keeps the detector armed while Jarvis is the selected Android assistant.
- Retains Side-button invocation and the compact assistant overlay.

## Build

- Android version: `18.3.1`
- APK artifact: `jarvis-assistant-v18.3.1-debug`
- Target ABI: `arm64-v8a`
