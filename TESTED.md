# Current validation status

This document records validation categories for the unified Jarvis product. It
does not replace CI logs, signed release manifests, runtime health, or physical
device evidence.

## Validated source and release

- Published product: `v19.0.0-alpha25`
- Release source: `f6db74ae6818833e8e197a0b20931e0dba4900b3`
- Authoritative branch: `jarvis/unified-production`
- Core application version: `3.7.0`
- Realtime protocol: `2`
- Android Phone and Wear versionCode: `190270`

Alpha25's tag, Core/Phone/Watch product manifest fields, and OTA manifest
`commitSha` resolve to the same release source. The production-signed Phone
and Watch APKs passed package, version, signature, manifest, resource, and
compiled-feature inspection before publication.

## Core and repository checks

The alpha25 source passed the repository's required GitHub checks:

- Core and tooling tests
- Ruff lint and format checks
- mypy static analysis
- Python compilation
- Bandit and dependency/security checks
- product-baseline and release-provenance assertions
- CodeQL analysis with no open code-scanning alert at publication

The final local regression run before alpha25 recorded 708 passing tests, 3
skipped tests, and 133 passing subtests. The skip and dependency warning status
remained visible rather than being converted into unsupported pass claims.

## Android Phone and Wear OS

The release workflow validated:

- shared `wearprotocol` tests
- Phone and Wear unit tests
- Phone and Wear release lint
- production release assembly from one source revision
- package `com.aaron.jarvisvoice`, version `19.0.0-alpha25`, versionCode
  `190270`, and the established production signing certificate
- Phone Integrations activity/deep link, Developer, delete-current-chat,
  realtime recovery, Wear bridge, assistant/overlay, and current branding
- Watch activity, Tile, assistant, voice, channel manager, and shared protocol

These are binary and automated checks. The alpha25 Phone and Watch have not
both completed final physical-device validation.

## Persistence and runtime

The unified deployment workflow performs read-only SQLite integrity checks
before and after Core replacement. During alpha25 preparation, all 20 active
Core and speaker databases passed `PRAGMA quick_check`, and existing persistent
mounts were retained. Core and Developer health reported the same alpha25
source revision after deployment.

Runtime validation covered Core health/readiness, Home Assistant connectivity,
External Agent health, follow-up worker/database health, conversation storage,
realtime availability, authenticated mobile provider state, and verified Home
Assistant action receipts. Google, Gmail, Calendar, Contacts, and Microsoft
truthfully remained Setup Required; Web and Home Assistant reported Connected.

## Physical validation status

Earlier alpha24 Phone evidence confirmed in-place OTA with data preservation,
realtime synchronization, text/voice operation, and verified Home Assistant
actions. That evidence is useful regression context but is not alpha25 physical
certification.

Current remaining physical validation:

- Phone: install alpha25 in place, retain settings/history, exercise text,
  voice, Integrations, Home Assistant receipt, assistant/overlay/wake, and
  confirm installed OTA integrity reporting.
- Watch: install alpha25 in place and exercise launch, Tile, microphone, Brain
  response, Phone/Watch continuity, endpoint routing, and audio output.

No document should claim those alpha25 physical checks passed until device
evidence exists.
