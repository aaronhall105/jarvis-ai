# Jarvis product baseline

`jarvis/unified-production` is the sole authoritative source for future Jarvis
Core deployments, Android and Wear builds, OTA manifests, and production
releases. The current published product is `v19.0.0-alpha26`, with Core
application version `3.7.0` and realtime protocol `2`.

## Lineage decision

No prior feature branch contained the complete product. The verified integrations
line has the authoritative Core, External Agent Platform, Google connectors,
security controls, planner, and action-receipt architecture. The supplied phone
and Watch hashes were traced to GitHub Actions artifact `9714544550`, run
`33251557799`, built from `jarvis/unified-production` commit
`1eb4c5913a3e69213ec45ad726bebb45779e3c01`. That existing source—not a
version-name guess or visual recreation—is the authoritative client baseline.
It already includes the unified-runtime Developer, routing, voice, recovery and
Wear work plus the Integrations UI.

The pre-reconciliation production container was built from `fa1273b7b550ef38a81f9bb86e1598d3139fc1c1`. Its tracked `bridge/app` files matched that commit byte-for-byte. It therefore had the integrations platform but not the later realtime turn ledger or unified-runtime web/Android/Wear features. Its `/app/config`, `/app/data`, and `/app/logs` mounts are persistent and must not be recreated during deployment.

## Mandatory current capabilities

### Core

- Persistent conversations and Memory
- Grounded Home Assistant reads and verified writes
- Fresh presence evidence and room-aware grounding
- Timezone-aware reminders, structured recurrence, verified condition watches,
  and retry-safe completion delivered once to the originating conversation
- Principal-scoped explicit personal memory with correction, recall, and forget
- Realtime turn admission, ledger, terminal states, response fencing, and recovery
- Proactive intelligence, vision/camera support, health, and observability

### External Agent and integrations

- Fail-closed planner and current capability validation
- Connector framework with principal/account isolation
- Approval enforcement and verified, durable action receipts
- Secure web/research tooling with SSRF and DNS-rebinding defenses
- Encrypted integration-account credentials
- Google OAuth with CSRF, callback replay, redirect, refresh, expiry, revocation, and health checks
- Gmail, Calendar, Contacts, and durable email/reply monitoring

### Android

- Package `com.aaron.jarvisvoice` and established production signer
- Exact launcher, wordmark, controls and navigation lineage proven by the
  supplied ground-truth phone APK
- Persistent chat history and delete-current-chat behavior
- Standard and Live voice, interruption, wake word, overlay, and default assistant
- Durable realtime delivery/recovery and current endpoint failover
- Developer/Codex mode and current menus/actions
- Top-level Integrations settings entry, `IntegrationsActivity`, and Google OAuth deep link
- Truthful Setup Required / Not Connected provider states
- Truthful staged-versus-installed OTA integrity evidence retained across an
  in-place update

### Wear OS

- Wear application and shared protocol modules
- Watch Tile and assistant endpoint
- Phone/watch microphone, speaker, and channel routing
- Stable turn epochs, startup buffering, and playback behavior

## Release policy

Release and deployment workflows must fail closed unless the source commit is
the current head of `origin/jarvis/unified-production`. One product release
records identical Jarvis/Core/phone/Watch source SHAs. OTA channel manifests
are GitHub Release assets rather than a development branch. The
product-baseline gate validates ground-truth source files, mandatory resources,
manifest routes, runtime markers, and workflow restrictions.

## Historical branch disposition

Every historical feature branch recorded in the machine-readable baseline was
audited before cleanup. Useful capabilities were reconciled into
`jarvis/unified-production`; final heads and lineage decisions were preserved
through archive tags, Git history, recovery evidence, and the consolidation
lineage manifest. The historical branches were then deleted from active local
and remote development.

The only active long-lived branch is now `jarvis/unified-production`.
Historical branch names and their original SHAs remain in the manifests as
intentional reconciliation evidence. They must not be interpreted as current
deployment, release, or contribution targets, and no workflow or active runtime
may depend on them.

The machine-readable authorities are
[`JARVIS_PRODUCT_BASELINE.json`](JARVIS_PRODUCT_BASELINE.json) and the explicit
capability/branch decision matrix
[`JARVIS_CONSOLIDATION_LINEAGE.json`](JARVIS_CONSOLIDATION_LINEAGE.json).
