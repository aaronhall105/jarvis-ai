# Current validation status

This document records validation categories for the unified Jarvis product. It
does not replace CI logs, signed release manifests, runtime health, or physical
device evidence.

## Validated source and release

- Current release candidate: `v19.0.0-alpha29`
- Release source: the immutable tag, product manifest, and OTA manifest must
  record the exact approved `jarvis/unified-production` revision
- Authoritative branch: `jarvis/unified-production`
- Core application version: `3.7.0`
- Realtime protocol: `2`
- Android Phone and Wear versionCode: `190310`

The tag-triggered workflow fails closed unless the tag equals the current
authoritative branch head. It builds Phone and Watch from that one revision,
verifies package/version/signing identity and compiled product markers, and
generates checksums, inspection reports, a product manifest, and OTA metadata
from the exact published bytes.

## Core and repository checks

The alpha29 release candidate adds compound multi-inbox cleanup, exact frozen
candidate sets, provider-isolated execution, and handled action-outcome status
for Android while preserving the already-shipped Email Assistant v3,
Outlook/Microsoft Graph, dialogue safety, and Gmail history resilience work.
Its complete test and release-gate evidence is recorded in the release PR and
immutable OTA workflow run rather than inferred here.

The protected release PR and post-merge alpha29 head must repeat the applicable
checks before the immutable release tag is created.

## Google Personal Integrations v1

Controlled live validation established:

- Google identity, Gmail, Calendar, and Contacts were connected and healthy;
  Microsoft remained truthfully Setup Required.
- Gmail inbox/read/thread, unread, sender, and subject searches returned real
  provider evidence.
- Gmail draft and reply-draft readback passed without being reported as sent.
- One explicitly approved self-addressed test email was sent exactly once and
  produced one verified send receipt. No external recipient was used.
- Calendar timezone, date-range, free/busy, temporary create, update, and
  cancel passed provider readback; the temporary event was removed.
- One durable Gmail monitor survived Core restart, completed exactly once,
  produced one verified completion receipt, and appended one assistant result
  to its originating conversation. Later worker cycles produced no duplicate.
- Encrypted credentials survived restart and all four Google providers remained
  connected and healthy.

Live Contacts provider health and no-result behavior passed. The connected
account did not contain suitable test contacts to prove both unique and
ambiguous-name resolution live. Automated tests cover both paths and fail
closed without inventing addresses.

## Android Phone and Wear OS

The alpha29 source retains the approved Phone/Watch lineage, shared protocol,
Integrations activity and OAuth deep link, Developer capability,
delete-current-chat, realtime recovery, assistant/overlay/wake support, Wear
bridge, Tile, voice, and current branding. The Android connected-provider
parser now renders null/missing/blank detail as no detail rather than the
literal text `null`; tests cover both empty detail and verified account email.

Package, version, versionCode, production signature, resources, manifest, and
compiled markers are verified again from the final signed alpha29 APKs during
publication.

## Persistence and runtime

The unified deployment workflow performs read-only SQLite integrity checks
before and after Core replacement. Google live validation preserved persistent
mounts and all Core/speaker stores passed `PRAGMA quick_check`. Conversations,
messages, memory, durable jobs, receipts, integration accounts, OAuth sessions,
and encrypted credentials remained intact.

Core and Developer report the deployed source revision. If exact alpha29
provenance requires redeployment after the release-only merge, the guarded
deployment path must preserve those stores and recheck provider health.

## Physical validation status

The alpha26 Phone completed an earlier in-place OTA with data preservation and
serves as the physical rollback baseline. Publishing alpha28 does not by itself
prove an alpha28 install.

- Phone alpha29: pending in-place user OTA validation.
- Watch alpha29: pending in-place user validation.

Neither application should be uninstalled or have its data cleared during
validation.
