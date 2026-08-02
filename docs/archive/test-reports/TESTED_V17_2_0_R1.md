# Jarvis v17.2.0-r1 validation

Completed before packaging:

- Python syntax compilation for the Realtime proxy, tests and patcher.
- Nine unit tests for session schema, PCM encoding, token handling, status secrecy and Jarvis tool calls.
- Patch simulation against the installed v17.0.3 Core layout.
- Five dependency-free Java tests for 24 kHz frame sizing and Core WebSocket URL construction.
- Four release-layout, Android version, permission and protocol-contract tests.
- Shell syntax validation.
- Successful installer simulation.
- Forced-failure rollback simulation, including restoration of Core, Android project, workflow and `.env`.

Still required after the release is pushed:

- GitHub Actions Android SDK 36 unit tests and debug APK compilation. This is the authoritative APK compilation check because the Jarvis server does not have an Android SDK.
- Live testing on Aaron's Samsung phone for microphone quality, network latency, echo cancellation, interruption and Realtime API behaviour.
