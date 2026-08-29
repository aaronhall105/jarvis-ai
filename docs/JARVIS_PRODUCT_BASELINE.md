# Jarvis product baseline

`jarvis/unified-production` is the sole authoritative source for future Jarvis Core deployments, Android and Wear builds, OTA manifests, and production releases.

## Lineage decision

No prior feature branch contained the complete product. The verified integrations line has the authoritative Core, External Agent Platform, Google connectors, security controls, planner, and action-receipt architecture. The unified-runtime line has the authoritative phone UI, Wear application, Developer/Codex experience, branding, endpoint routing, voice ownership fencing, and realtime recovery work. The unified product deliberately combines those lineages instead of treating either branch as globally newer.

The pre-reconciliation production container was built from `fa1273b7b550ef38a81f9bb86e1598d3139fc1c1`. Its tracked `bridge/app` files matched that commit byte-for-byte. It therefore had the integrations platform but not the later realtime turn ledger or unified-runtime web/Android/Wear features. Its `/app/config`, `/app/data`, and `/app/logs` mounts are persistent and must not be recreated during deployment.

## Mandatory current capabilities

### Core

- Persistent conversations and Memory
- Grounded Home Assistant reads and verified writes
- Fresh presence evidence and room-aware grounding
- Durable, retry-safe follow-ups delivered to the originating conversation
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
- Latest approved launcher, wordmark, and status branding
- Persistent chat history and delete-current-chat behavior
- Standard and Live voice, interruption, wake word, overlay, and default assistant
- Durable realtime delivery/recovery and current endpoint failover
- Developer/Codex mode and current menus/actions
- Top-level Integrations settings entry, `IntegrationsActivity`, and Google OAuth deep link
- Truthful Setup Required / Not Connected provider states

### Wear OS

- Wear application and shared protocol modules
- Watch Tile and assistant endpoint
- Phone/watch microphone, speaker, and channel routing
- Stable turn epochs, startup buffering, and playback behavior

## Release policy

Release and deployment workflows must fail closed unless the source commit is the current head of `origin/jarvis/unified-production`. The product-baseline gate validates mandatory files, resources, manifest routes, runtime markers, and workflow restrictions. Historical feature branches remain recovery references only and must not be used to build releases.

## Historical branch retention

The audited heads recorded in the machine-readable manifest must remain available until the unified Core, phone APK, Wear APK, OTA feed, GitHub CI, and on-device update have all been verified. After that point, archival tags should preserve each recorded head before any remote branch is deleted. The old `conversation-engine`, `jarvis/production-alpha14`, `jarvis/wear-v1`, `jarvis/unified-runtime-v1`, `jarvis/alpha19-production-hardening`, `jarvis/external-agent-platform`, and `jarvis/integrations-accounts-v1` branches are then candidates for deletion from active development, but their history is not to be removed as part of this reconciliation.

The machine-readable authority for this policy is [`JARVIS_PRODUCT_BASELINE.json`](JARVIS_PRODUCT_BASELINE.json).
