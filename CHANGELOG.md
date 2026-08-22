# Changelog

## Current default branch

### v19.0.0-alpha17

The authoritative default-branch release identity is `19.0.0-alpha17`.

Recent release-line changes after Alpha13 include:

- **Alpha14** — production-hardening work.
- **Alpha15** — Android OTA updater plus signing/artifact verification hardening.
- **Alpha16** — voice-output correction work.
- **Alpha17** — restored the original ElevenLabs Jarvis voice route and continued
  realtime playback/echo handling.

The full source history remains the authority for commit-level detail.

## Active development

### Alpha19 production hardening — draft

[PR #1](https://github.com/aaronhall105/jarvis-ai/pull/1) is open as a draft and
is not yet part of the default branch.

Its stated scope includes safer voice interruption, room-aware recognition,
reliability telemetry, Memory v4, explainable proactive/camera decisions and
additional repository/CI hardening.

## Earlier release notes

The last dedicated root release note is:

- [v19.0.0-alpha13 — Final Polish, Persistent Wake and Adaptive Replies](CHANGES_V19_0_0_ALPHA13.md)

Older release notes are preserved under
[`docs/releases/archive/`](docs/releases/archive/) for traceability.

Historical documents may contain old product identities and should not be used
as the current release source of truth. Use `bridge/app/version.py` and
[PROJECT_STATUS.md](PROJECT_STATUS.md).
