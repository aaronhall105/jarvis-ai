# Jarvis v17.3.0 validation

Completed before packaging:

- Python syntax compilation for the unified Realtime proxy, patcher and tests.
- Twelve dependency-free Core tests covering authoritative Jarvis routing, Amber transcription guidance, voice modes, response rendering, stale-turn suppression, status secrecy and token validation.
- Patch simulation against a v17.2.0-r1 Core layout.
- Dependency-free Java tests for wake phrase parsing, voice selection, Core URL construction and 24 kHz frame sizing.
- Android source contract tests for version, permissions, voice settings, wake engine, original TTS client and unified protocol events.
- Shell syntax validation.
- Release-layout validation.
- Full installer success simulation, including Core rebuild readiness, production `websockets` import, unified-brain status and removal of superseded Android workflows.
- Forced Docker-build failure simulation with content-exact rollback of Core, Android client, workflows and `.env`.
- Exact packaged-archive extraction and successful installer simulation from a reconstructed v17.2.0-r1 repository.

Still required after upload:

- Installer execution against Aaron's live v17.2.0-r1 server.
- GitHub Actions Android SDK 36 unit tests and APK compilation.
- Phone testing for wake reliability, `Where is Amber?`, conversation follow-ups, each voice, original Home Assistant TTS, interruption and sleep/wake transitions.

A dedicated neural keyword-spotting model is not embedded in this release. Wake phrase mode uses Android's on-device recogniser when available.
