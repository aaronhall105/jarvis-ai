# Jarvis AI project status

_Last reviewed against the `conversation-engine` branch on 22 August 2026._

This file defines the public shipped-versus-development boundary for the
repository.

## Current default branch

The authoritative release identity in `bridge/app/version.py` is:

| Identity | Value |
| --- | --- |
| Jarvis release | `19.0.0-alpha17` |
| Core application API | `3.7.0` |
| Realtime protocol | `2` |
| Default branch | `conversation-engine` |

`bridge/app/version.py` is the source of truth when a historical document,
release note or archived installer contains an older version.

## Active development

[PR #1 — Jarvis 19 alpha19 production hardening](https://github.com/aaronhall105/jarvis-ai/pull/1)
is an **open draft** and is not part of the default branch until merged.

The PR describes work on:

- generation-safe voice interruption across Core, TTS and Android playback;
- room-aware recognition and correction/reliability telemetry;
- Memory v4 history, provenance, expiry, retirement and restore;
- explainable proactive/camera initiative decisions;
- approval-only learning proposals;
- repository cleanup and broader scheduled CI coverage.

Treat those capabilities as candidate Alpha19 work, not as shipped Alpha17
behaviour.

## Capability maturity

| Capability | Status |
| --- | --- |
| Docker-hosted Jarvis Core | Implemented on default branch |
| Android chat and assistant client | Implemented on default branch |
| Home Assistant conversation integration | Implemented on default branch |
| Persistent conversation and household context | Implemented on default branch |
| Offline wake/re-arm behaviour | Alpha, implemented on default branch |
| Realtime voice and interruption | Alpha, actively hardened |
| Proactive household alerts | Implemented, deployment-specific |
| Frigate/vision intelligence | Implemented, deployment-specific |
| Multi-user Voice ID | Implemented, convenience identity only |
| Supervised self-improvement | Experimental, human approval required |
| Alpha19 Memory v4 and reliability hardening | Draft PR / not shipped |

## Evidence and validation

Current repository quality gates are defined in
`.github/workflows/jarvis-ci.yml` and include:

- Python compilation;
- Core pytest regression tests;
- Home Assistant integration/package checks;
- Ruff correctness checks;
- Bandit high-severity scanning;
- Python dependency auditing;
- Android unit tests and debug assembly;
- pull-request dependency review.

CodeQL and Android OTA workflows are maintained separately under
`.github/workflows/`.

Historical validation reports remain useful for traceability but must not be
presented as current whole-product certification.

## Documentation rule

When updating Jarvis:

1. update `bridge/app/version.py` as the release source of truth;
2. update this status page when the shipped/development boundary changes;
3. update `CHANGELOG.md` for user-visible changes;
4. update README/installation links only when a published artifact is verified;
5. preserve old release material under `docs/archive/` or
   `docs/releases/archive/` rather than presenting it as current.
