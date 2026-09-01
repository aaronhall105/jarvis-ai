# Jarvis documentation

Documentation is divided between the current unified product and historical
material retained for traceability. Current development, deployment, and
release instructions apply only to `jarvis/unified-production`.

## Current product documentation

### Start here

- [Installation](../INSTALL.md)
- [Architecture](../ARCHITECTURE.md)
- [Changelog](../CHANGELOG.md)
- [Validation status](../TESTED.md)
- [Product baseline](JARVIS_PRODUCT_BASELINE.md)
- [Machine-readable product baseline](JARVIS_PRODUCT_BASELINE.json)
- [Contributing](../CONTRIBUTING.md)
- [Security](../SECURITY.md)
- [Alpha27 release notes](releases/CHANGES_V19_0_0_ALPHA27.md)

### Capabilities and services

- [Personal Assistant v1](PERSONAL_ASSISTANT_V1.md)
- [Integrations & Accounts](INTEGRATIONS_ACCOUNTS_V1.md)
- [External Agent Platform](EXTERNAL_AGENT_PLATFORM.md)
- [Developer mode](developer-mode.md)
- [Self-improvement](SELF_IMPROVEMENT.md)
- [Proactive orchestrator](PROACTIVE_ORCHESTRATOR.md)
- [Temporal action engine](TEMPORAL_ACTION_ENGINE.md)
- [Subject-aware memory](SUBJECT_AWARE_MEMORY.md)
- [Subject memory retrieval](SUBJECT_MEMORY_RETRIEVAL.md)
- [Voice identity](VOICE_ID.md)

### Clients and releases

- [Android Phone/Wear project](../android/jarvis-voice-client/README.md)
- [Android OTA and release process](ANDROID_OTA_RELEASES.md)
- [Wear endpoint](wear-endpoint-v1.md)
- [Release notes index](releases/README.md)

### Configuration and reference

- [Entity map](reference/ENTITY_MAP.md)

Deployment secrets, local endpoints, credentials, databases, conversation
history, memory, and signing files are intentionally not documentation assets
and must remain outside Git.

## Historical and archived documentation

Historical files document earlier product versions or the completed
reconciliation. They are evidence, not current installation or release
instructions.

- [Historical feature notes](archive/feature-notes/)
- [Historical install guides](archive/install-guides/)
- [Historical product manifests](archive/manifests/)
- [Historical test reports](archive/test-reports/)
- [Historical release notes](releases/archive/)
- [Historical configuration notes](configuration/)
- [Consolidation lineage evidence](JARVIS_CONSOLIDATION_LINEAGE.json)

Old branch names and source SHAs may appear in baseline and lineage evidence.
That is intentional: the branches were audited, archived, reconciled, and
removed from active development.
