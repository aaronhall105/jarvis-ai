# Current validation status

This document records validation categories for the unified Jarvis product. It
does not replace CI logs, signed release manifests, runtime health, or physical
device evidence.

## Validated source and release

- Published product: `v19.0.0-alpha26`
- Release source: the immutable tag, product manifest, and OTA manifest record
  the exact approved `jarvis/unified-production` revision
- Authoritative branch: `jarvis/unified-production`
- Core application version: `3.7.0`
- Realtime protocol: `2`
- Android Phone and Wear versionCode: `190280`

Alpha26's tag, Core/Phone/Watch product manifest fields, and OTA manifest
`commitSha` resolve to the same release source. The production-signed Phone
and Watch APKs passed package, version, signature, manifest, resource, and
compiled-feature inspection before publication.

## Core and repository checks

The alpha26 source passed the repository's required GitHub checks:

- Core and tooling tests
- Ruff lint and format checks
- mypy static analysis
- Python compilation
- Bandit and dependency/security checks
- product-baseline and release-provenance assertions
- CodeQL analysis with no open code-scanning alert at publication

The final Personal Assistant v1 regression run before alpha26 recorded 736
passing tests, 3 skipped tests, and 133 passing subtests. The skip and
dependency warning status
remained visible rather than being converted into unsupported pass claims.

## Android Phone and Wear OS

The release workflow validated:

- shared `wearprotocol` tests
- Phone and Wear unit tests
- Phone and Wear release lint
- production release assembly from one source revision
- package `com.aaron.jarvisvoice`, version `19.0.0-alpha26`, versionCode
  `190280`, and the established production signing certificate
- Phone Integrations activity/deep link, Developer, delete-current-chat,
  realtime recovery, Wear bridge, assistant/overlay, and current branding
- Watch activity, Tile, assistant, voice, channel manager, and shared protocol

These are binary and automated checks. The alpha26 Phone and Watch have not
both completed final physical-device validation.

## Persistence and runtime

The unified deployment workflow performs read-only SQLite integrity checks
before and after Core replacement. During alpha26 preparation, all 20 active
Core and speaker databases passed `PRAGMA quick_check`, and existing persistent
mounts were retained. Core and Developer health reported the same verified
unified source revision before release preparation; final alpha26 provenance is
rechecked after the release-preparation merge.

Runtime validation covered Core health/readiness, Home Assistant connectivity,
External Agent health, follow-up worker/database health, conversation storage,
realtime availability, authenticated mobile provider state, and verified Home
Assistant action receipts. Google, Gmail, Calendar, Contacts, and Microsoft
truthfully remained Setup Required; Web and Home Assistant reported Connected.

Personal Assistant v1 live validation covered reminder execution, Core restart
persistence, recurring work, cancellation, rescheduling, verified Home
Assistant condition evaluation, same-conversation completion, explicit-memory
save/restart/recall/correction/forget, and duplicate/idempotency fencing.
Notification transport produced truthful failed and accepted-but-unverified
states without replacing the durable conversation result.

## Physical validation status

Earlier alpha24 Phone evidence confirmed in-place OTA with data preservation,
realtime synchronization, text/voice operation, and verified Home Assistant
actions. That evidence is useful regression context but is not alpha26 physical
certification.

Current remaining physical validation:

- Phone: install alpha26 in place, retain settings/history, exercise text,
  voice, Integrations, Home Assistant receipt, assistant/overlay/wake, and
  confirm installed OTA integrity reporting.
- Watch: install alpha26 in place and exercise launch, Tile, microphone, Brain
  response, Phone/Watch continuity, endpoint routing, and audio output.

No document should claim those alpha26 physical checks passed until device
evidence exists.
