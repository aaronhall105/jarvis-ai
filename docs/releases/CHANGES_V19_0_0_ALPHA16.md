# Jarvis 19.0.0-alpha16

Alpha16 fixes production voice output when the configured Home Assistant Assist pipeline cannot render TTS. Jarvis continues to use Home Assistant audio when available and now falls back to the existing Android speech engine only after an explicit pre-playback rendering failure.

The fallback is fenced to the active voice turn, cannot duplicate media that has already started, and remains cancellable for interruption and barge-in. Production-safe output-stage diagnostics cover renderer selection, playback start/completion, and TTS errors without logging speech content or credentials.

Core application version remains 3.7.0 and realtime protocol remains 2 because this release introduces no Core API or realtime protocol incompatibility.
