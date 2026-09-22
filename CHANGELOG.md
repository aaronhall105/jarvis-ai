# Changelog

## Current prerelease

### v19.0.0-alpha30 — OTA recovery and safe multi-inbox cleanup

Alpha30 contains the approved compound Gmail and Outlook cleanup work prepared
for alpha29, plus a release-pipeline repair that avoids requesting the removed
legacy Android SDK `tools` package. Alpha29's immutable tag was consumed by the
failed setup run and did not publish APKs or update the OTA channel.

See [the complete alpha30 release notes](docs/releases/CHANGES_V19_0_0_ALPHA30.md).

Core application version remains `3.7.0`; realtime protocol remains `2`.

## Historical releases

### v19.0.0-alpha29 — Safe multi-inbox cleanup

Alpha29 adds compound Gmail and Outlook cleanup requests with durable provider
scope, exact frozen candidate sets, explicit confirmation, recoverable-only
destinations, truthful partial-provider results, and structured handled-action
outcomes for Android. It preserves Email Assistant v3, Smart Important Only,
and the optional Astra Executive Agent architecture already deployed in Core.

See [the complete alpha29 release notes](docs/releases/CHANGES_V19_0_0_ALPHA29.md).

Core application version remains `3.7.0`; realtime protocol remains `2`.

### v19.0.0-alpha28 — Email Assistant v3 and Outlook

Alpha28 packages the already-merged Email Assistant v3 and its unified Gmail
and Outlook/Microsoft 365 provider architecture. It adds Microsoft Graph and
the Android Outlook connection controls, durable dialogue continuations,
bounded frozen-set bulk cleanup, provider-specific Bin/Deleted Items language,
and resilient Gmail incremental-history handling.

Outlook support is included, but it remains **Setup Required** until Microsoft
application configuration and Aaron's browser consent are complete. See
[the complete alpha28 release notes](docs/releases/CHANGES_V19_0_0_ALPHA28.md).

Core application version remains `3.7.0`; realtime protocol remains `2`.

### v19.0.0-alpha27 — Google Personal Integrations v1

Alpha27 packages the live-verified Google Personal Integrations v1 runtime with
Core, Developer gateway, Android Phone, Wear OS, product manifest, and OTA
metadata from one approved `jarvis/unified-production` revision. It connects
Google OAuth, Gmail, Calendar, Contacts, durable Gmail monitoring, verified
action receipts, and Personal Assistant same-conversation delivery through the
existing unified capability architecture. It also fixes Android connected
provider cards rendering a literal `null` detail.

See [the complete alpha27 release notes](docs/releases/CHANGES_V19_0_0_ALPHA27.md)
and the [published GitHub prerelease](https://github.com/aaronhall105/jarvis-ai/releases/tag/v19.0.0-alpha27).

Core application version remains `3.7.0`; realtime protocol remains `2`.

Recent prerelease notes are indexed in
[`docs/releases/README.md`](docs/releases/README.md). Older version-specific
release notes remain under [`docs/releases/archive/`](docs/releases/archive/)
for traceability and are not current installation instructions.

Alpha26 remains available as
[historical release documentation](docs/releases/CHANGES_V19_0_0_ALPHA26.md).

The former root alpha13 note is preserved as
[historical alpha13 release documentation](docs/releases/archive/CHANGES_V19_0_0_ALPHA13.md).
